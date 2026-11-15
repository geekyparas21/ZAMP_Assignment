"""Image invoices, end to end through the real pipeline.

Phone photos and screenshots are how invoices actually arrive, so they get the
same treatment as a PDF: the whole run, against a throwaway database, with the
model's response supplied from a recorded fixture so nothing here touches the
network.

What these tests are really guarding is that nothing downstream of ingest
knows or cares which format the document arrived in.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlmodel import Session, select

from .conftest import encode, image_upload, render_sample
from .test_persistence import StubExtractor

D = Decimal


def run(data: bytes, filename: str, stem: str | None = None,
        sample_key: str | None = None) -> tuple[str, str]:
    """Run one document through the pipeline. Returns (run_id, outcome)."""
    from backend.app.pipeline.runner import execute_run, new_run_id

    run_id = new_run_id()
    extractor = StubExtractor(stem) if stem else None
    outcome = execute_run(run_id, data, filename, sample_key, extractor)
    return run_id, outcome


def load(temp_db, run_id: str):
    from backend.app.models import Run, RunStep, StoredFile

    with Session(temp_db.get_engine()) as session:
        run = session.get(Run, run_id)
        steps = session.exec(
            select(RunStep).where(RunStep.run_id == run_id)).all()
        stored = session.get(StoredFile, run_id)
        return run, {s.stage: s for s in steps}, stored


# ===========================================================================
# The happy path, in both image formats
# ===========================================================================
@pytest.mark.parametrize("fmt,filename,media_type", [
    ("PNG", "invoice.png", "image/png"),
    ("JPEG", "invoice.jpg", "image/jpeg"),
])
def test_an_image_invoice_runs_end_to_end_and_is_approved(
        fmt, filename, media_type, temp_db):
    """Same invoice, same decision, whether it arrives as a PDF or a photo."""
    run_id, outcome = run(image_upload(fmt), filename,
                          "00_happy_path_bharat_steel")
    row, steps, stored = load(temp_db, run_id)

    assert outcome == "APPROVED"
    assert row.status == "completed"
    assert row.file_kind == "image"
    assert row.is_scanned is True
    assert row.po_number == "PO-4501"
    assert row.total_amount == D("107026.00")

    # The original bytes are kept as they arrived, so the preview panel can
    # show the photo the decision was actually made from.
    assert stored.content_type == media_type
    assert stored.content == image_upload(fmt)


def test_the_stage_summary_says_why_it_went_to_vision(temp_db):
    run_id, _ = run(image_upload("JPEG", size=(1500, 2000)), "photo.jpg",
                    "00_happy_path_bharat_steel")
    _, steps, _ = load(temp_db, run_id)

    analysis = steps["document_analysis"]
    assert analysis.status == "passed"
    assert "Image upload" in analysis.summary
    assert "JPG" in analysis.summary
    assert "1500" in analysis.summary and "2000" in analysis.summary
    assert "vision extraction" in analysis.summary

    detail = analysis.detail
    assert detail["file_kind"] == "image"
    assert detail["image"]["original_width"] == 1500
    assert detail["image"]["processed_width"] == 1500


def test_the_ingest_stage_records_the_file_kind_for_the_badge(temp_db):
    run_id, _ = run(image_upload("PNG"), "shot.png",
                    "00_happy_path_bharat_steel")
    _, steps, _ = load(temp_db, run_id)

    ingest = steps["ingest"]
    assert ingest.detail["file_kind_label"] == "Image"
    assert "Image" in ingest.summary


def test_the_recorded_dimensions_show_the_downscale_that_was_applied(temp_db, rules):
    max_edge = rules.integer("ingest.image.max_long_edge_px")
    big = encode(render_sample().resize((2400, 3400)), "JPEG")

    run_id, outcome = run(big, "big-photo.jpg", "00_happy_path_bharat_steel")
    _, steps, _ = load(temp_db, run_id)
    image = steps["document_analysis"].detail["image"]

    assert outcome == "APPROVED"
    assert image["original_height"] == 3400
    assert image["processed_height"] == max_edge
    assert image["downscaled"] is True


# ===========================================================================
# Rotation
# ===========================================================================
def test_a_rotated_photo_is_straightened_before_extraction(temp_db):
    """EXIF orientation 6 - the way a phone held upright usually stores it."""
    upright = render_sample()
    run_id, outcome = run(encode(upright, "JPEG", exif_orientation=6),
                          "rotated.jpg", "00_happy_path_bharat_steel")
    row, steps, _ = load(temp_db, run_id)
    image = steps["document_analysis"].detail["image"]

    assert outcome == "APPROVED"
    assert image["exif_orientation"] == 6
    assert image["exif_rotation_applied"] is True
    # Portrait again: the width recorded is the displayed width, not the
    # sideways one the file stored.
    assert image["original_width"] == upright.size[0]
    assert image["original_height"] == upright.size[1]
    assert "EXIF rotation applied" in steps["document_analysis"].summary


# ===========================================================================
# Quality
# ===========================================================================
def test_a_low_resolution_photo_is_held_for_a_human(temp_db, rules):
    """A decision read off an unreadable photo must not be an approval."""
    floor = rules.integer("ingest.image.low_quality_short_edge_px")
    tiny = encode(render_sample().resize((floor - 200, floor)), "JPEG")

    run_id, outcome = run(tiny, "tiny.jpg", "00_happy_path_bharat_steel")
    row, steps, _ = load(temp_db, run_id)

    assert outcome == "NEEDS_REVIEW"
    assert "LOW_IMAGE_QUALITY" in [f["code"] for f in row.findings]
    assert steps["document_analysis"].status == "warning"
    assert row.explanation and row.next_action


# ===========================================================================
# Refusals
# ===========================================================================
@pytest.mark.parametrize("data,filename,expected", [
    (b"GIF89a" + b"0" * 2000, "scan.gif", "GIF image"),
    (b"Dear sir, our invoice is attached." * 20, "invoice.png",
     "unrecognised format"),
])
def test_an_unsupported_file_fails_with_a_message_listing_the_formats(
        data, filename, expected, temp_db):
    run_id, outcome = run(data, filename)
    row, steps, _ = load(temp_db, run_id)

    assert outcome == "FAILED"
    assert row.status == "failed"
    assert row.top_reason == "FILE_TYPE_UNSUPPORTED"
    assert expected in row.explanation
    for fmt in ("PDF", "PNG", "JPG", "WEBP"):
        assert fmt in row.explanation
    assert row.next_action
    assert steps["ingest"].status == "failed"


def test_an_oversized_file_fails_with_a_clear_message(temp_db, rules):
    limit_mb = int(rules.number("ingest.max_file_mb"))
    oversized = image_upload("PNG") + b"\x00" * (limit_mb * 1024 * 1024)

    run_id, outcome = run(oversized, "huge.png")
    row, _, _ = load(temp_db, run_id)

    assert outcome == "FAILED"
    assert row.top_reason == "FILE_TOO_LARGE"
    assert f"{limit_mb} MB limit" in row.explanation
    assert row.next_action


# ===========================================================================
# The bundled photo sample
# ===========================================================================
def test_the_photo_sample_approves_against_its_po(temp_db):
    """The sample gallery's phone photo, on its recorded extraction.

    Nothing is wrong with this invoice - it is simply a JPG. If the image
    path works, it approves and moves the PO balance like any other.
    """
    from pathlib import Path

    from backend.app.models import PurchaseOrderRow

    data = Path("sample_invoices/10_phone_photo_vega.jpg").read_bytes()
    run_id, outcome = run(data, "10_phone_photo_vega.jpg",
                          "10_phone_photo_vega", "10_phone_photo_vega")
    row, _, _ = load(temp_db, run_id)

    assert outcome == "APPROVED"
    assert row.file_kind == "image"
    assert row.vendor_id == "V008"
    assert row.po_number == "PO-4580"
    assert row.total_amount == D("130980.00")

    with Session(temp_db.get_engine()) as session:
        po = session.get(PurchaseOrderRow, "PO-4580")
        assert po.amount_invoiced_to_date == D("130980.00")
        assert po.status == "closed", "the PO is fully billed by this invoice"
