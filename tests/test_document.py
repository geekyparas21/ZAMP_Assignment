"""Ingest and document analysis - the stages before any model is involved."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from backend.app.rules.codes import Finding
from backend.app.services.document import IngestError, analyse, detect, ingest

from .conftest import encode, image_upload, render_sample

SAMPLES = Path(__file__).resolve().parents[1] / "sample_invoices"


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


# --- ingest ----------------------------------------------------------------
def test_a_real_pdf_is_accepted_and_hashed():
    digest, size, kind = ingest(sample("00_happy_path_bharat_steel.pdf"))
    assert len(digest) == 64
    assert size > 1000
    assert kind == "pdf"


def test_the_same_bytes_always_hash_the_same():
    """Duplicate-by-hash detection depends on this."""
    data = sample("00_happy_path_bharat_steel.pdf")
    assert ingest(data)[0] == ingest(data)[0]


def test_different_invoices_hash_differently():
    a = ingest(sample("00_happy_path_bharat_steel.pdf"))[0]
    b = ingest(sample("03_duplicate_vega.pdf"))[0]
    assert a != b


def test_an_image_is_hashed_the_same_way_a_pdf_is():
    """Duplicate-by-hash must work identically whatever the format."""
    data = image_upload("PNG")
    assert ingest(data)[0] == ingest(data)[0]
    assert ingest(data)[2] == "image"


def test_an_empty_file_is_refused_clearly():
    with pytest.raises(IngestError) as exc:
        ingest(b"")
    assert exc.value.code == "FILE_EMPTY"


@pytest.mark.parametrize("fmt,label", [
    ("PNG", "PNG"), ("JPEG", "JPG"), ("WEBP", "WEBP"),
])
def test_every_accepted_image_format_is_recognised_from_its_bytes(fmt, label):
    kind, detected, _ = detect(image_upload(fmt))
    assert (kind, detected) == ("image", label)


def test_the_extension_is_never_trusted_over_the_content():
    """A PDF renamed .png is still a PDF, and a .txt renamed .png is not one."""
    assert detect(sample("00_happy_path_bharat_steel.pdf"), "invoice.png")[0] == "pdf"
    with pytest.raises(IngestError) as exc:
        ingest(b"Dear sir, please find our invoice attached.", "invoice.png")
    assert exc.value.code == "FILE_TYPE_UNSUPPORTED"


@pytest.mark.parametrize("data,filename,label", [
    (b"GIF89a" + b"0" * 100, "scan.gif", "GIF image"),
    (b"PK\x03\x04" + b"0" * 100, "invoice.docx", "ZIP / Office document"),
    (b"BM" + b"0" * 100, "invoice.bmp", "BMP image"),
    (b"II*\x00" + b"0" * 100, "invoice.tif", "TIFF image"),
    (b'{"total": 1}', "invoice.json", "JSON or text file"),
])
def test_unsupported_uploads_are_refused_and_named(data, filename, label):
    with pytest.raises(IngestError) as exc:
        ingest(data, filename)
    assert exc.value.code == "FILE_TYPE_UNSUPPORTED"
    assert exc.value.evidence["detected"] == label


def test_the_refusal_lists_what_would_have_been_accepted():
    with pytest.raises(IngestError) as exc:
        ingest(b"GIF89a" + b"0" * 100, "scan.gif")
    message = Finding(exc.value.code, "ingest", exc.value.evidence).message
    for fmt in ("PDF", "PNG", "JPG", "WEBP"):
        assert fmt in message, message


def test_an_unknown_signature_falls_back_to_the_file_extension():
    """Naming the extension tells a user more than 'unsupported' alone."""
    with pytest.raises(IngestError) as exc:
        ingest(b"mystery bytes", "thing.bin")
    assert ".bin" in exc.value.evidence["detected"]


def test_a_file_over_the_limit_is_refused_before_anything_else(rules):
    limit_mb = rules.number("ingest.max_file_mb")
    oversized = b"%PDF-" + b"0" * int(limit_mb * 1024 * 1024)
    with pytest.raises(IngestError) as exc:
        ingest(oversized, "huge.pdf", rules)
    assert exc.value.code == "FILE_TOO_LARGE"
    message = Finding(exc.value.code, "ingest", exc.value.evidence).message
    assert str(int(limit_mb)) in message


def test_a_file_at_the_limit_is_still_accepted(rules):
    at_limit = b"%PDF-" + b"0" * (int(rules.number("ingest.max_file_mb")
                                      * 1024 * 1024) - 5)
    assert ingest(at_limit, "big.pdf", rules)[1] == len(at_limit)


def test_a_truncated_pdf_is_reported_as_corrupt(rules):
    broken = sample("00_happy_path_bharat_steel.pdf")[:400]
    with pytest.raises(IngestError) as exc:
        analyse(broken, rules)
    assert exc.value.code == "FILE_CORRUPT"
    assert exc.value.evidence.get("detail")


# --- analysis: PDFs --------------------------------------------------------
def test_a_text_pdf_takes_the_text_path(rules):
    info, findings = analyse(sample("00_happy_path_bharat_steel.pdf"), rules)
    assert info.is_scanned is False
    assert info.kind == "pdf_text"
    assert info.kind_label == "PDF"
    assert info.page_images == [], "text PDFs must not be rendered needlessly"
    assert "Bharat Steel" in info.text
    assert findings == []


def test_a_scanned_pdf_takes_the_vision_path(rules):
    info, findings = analyse(sample("01_scanned_no_po_deccan.pdf"), rules)
    assert info.is_scanned is True
    assert info.kind == "pdf_scanned"
    assert info.kind_label == "Scanned PDF"
    assert info.chars_per_page == 0.0
    assert len(info.page_images) == info.page_count
    assert info.page_images[0][:4] == b"\x89PNG"
    assert [f.code for f in findings] == ["SCANNED_DOCUMENT"]


def test_the_scanned_finding_explains_why_it_chose_that_path(rules):
    _, findings = analyse(sample("01_scanned_no_po_deccan.pdf"), rules)
    message = findings[0].message
    assert "No text layer" in message
    assert "0 characters" in message


def test_every_sample_is_routed_and_none_crash(rules):
    for pdf in sorted(SAMPLES.glob("*.pdf")):
        info, _ = analyse(pdf.read_bytes(), rules)
        assert info.page_count >= 1
        assert info.is_scanned == (pdf.name.startswith("01_scanned")), pdf.name
        assert info.kind in ("pdf_text", "pdf_scanned")


# --- analysis: images ------------------------------------------------------
@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_an_image_skips_the_text_check_and_goes_to_vision(fmt, rules):
    info, findings = analyse(image_upload(fmt), rules, f"invoice.{fmt.lower()}")

    assert info.kind == "image"
    assert info.is_scanned is True, "an image has no text layer to fall back to"
    assert info.text == ""
    assert info.page_count == 1
    assert len(info.page_images) == 1
    assert "IMAGE_UPLOAD" in [f.code for f in findings]


def test_the_image_finding_says_which_format_and_size_it_was(rules):
    _, findings = analyse(image_upload("JPEG"), rules, "photo.jpg")
    message = next(f for f in findings if f.code == "IMAGE_UPLOAD").message
    assert "JPG" in message
    assert "vision" in message


def test_a_large_photo_is_downscaled_to_the_configured_long_edge(rules):
    max_edge = rules.integer("ingest.image.max_long_edge_px")
    big = encode(render_sample().resize((2400, 3400)), "JPEG")

    info, _ = analyse(big, rules, "photo.jpg")
    detail = info.image

    assert (detail["original_width"], detail["original_height"]) == (2400, 3400)
    assert max(detail["processed_width"], detail["processed_height"]) == max_edge
    assert detail["downscaled"] is True
    # The aspect ratio must survive, or the model is reading a squashed page.
    assert abs(detail["processed_width"] / detail["processed_height"]
               - 2400 / 3400) < 0.01


def test_an_image_already_under_the_limit_is_left_alone(rules):
    info, _ = analyse(image_upload("PNG", size=(900, 1200)), rules, "shot.png")
    detail = info.image
    assert detail["downscaled"] is False
    assert (detail["processed_width"], detail["processed_height"]) == (900, 1200)


@pytest.mark.parametrize("orientation", [3, 6, 8])
def test_exif_orientation_is_applied_before_anything_is_sent(orientation, rules):
    """A phone stores the sensor's view plus a tag saying which way is up.

    Without this the model is handed a sideways invoice, which is the single
    most common reason a photographed invoice extracts badly.
    """
    upright = render_sample()
    rotated = encode(upright, "JPEG", exif_orientation=orientation)

    info, _ = analyse(rotated, rules, "photo.jpg")
    detail = info.image

    assert (detail["original_width"], detail["original_height"]) == upright.size
    assert detail["exif_orientation"] == orientation
    assert detail["exif_rotation_applied"] is True

    with Image.open(io.BytesIO(info.page_images[0])) as sent:
        assert sent.size == upright.size, "the model must be sent it upright"


def test_an_image_without_an_orientation_tag_is_not_rotated(rules):
    upright = render_sample()
    info, _ = analyse(encode(upright, "PNG"), rules, "shot.png")
    assert info.image["exif_rotation_applied"] is False
    assert (info.image["processed_width"],
            info.image["processed_height"]) == upright.size


def test_a_low_resolution_image_is_flagged_for_a_human(rules):
    floor = rules.integer("ingest.image.low_quality_short_edge_px")
    tiny = encode(render_sample().resize((floor - 200, floor)), "JPEG")

    info, findings = analyse(tiny, rules, "tiny.jpg")
    codes = [f.code for f in findings]

    assert "LOW_IMAGE_QUALITY" in codes
    low = next(f for f in findings if f.code == "LOW_IMAGE_QUALITY")
    assert low.severity.name == "REVIEW", "a blurry photo must not auto-approve"
    assert str(floor) in low.message


def test_a_readable_image_carries_no_quality_warning(rules):
    _, findings = analyse(image_upload("JPEG"), rules, "photo.jpg")
    assert "LOW_IMAGE_QUALITY" not in [f.code for f in findings]


def test_a_corrupt_image_is_reported_as_unreadable(rules):
    truncated = image_upload("PNG")[:120]
    with pytest.raises(IngestError) as exc:
        analyse(truncated, rules, "broken.png")
    assert exc.value.code == "FILE_IMAGE_UNREADABLE"


def test_the_photo_sample_takes_the_image_path(rules):
    info, findings = analyse(sample("10_phone_photo_vega.jpg"), rules,
                             "10_phone_photo_vega.jpg")
    assert info.kind == "image"
    assert info.kind_label == "Image"
    assert info.media_type == "image/jpeg"
    assert [f.code for f in findings] == ["IMAGE_UPLOAD"]
    assert info.image["downscaled"] is True
