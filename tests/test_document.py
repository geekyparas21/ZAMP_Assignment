"""Ingest and document analysis - the stages before any model is involved."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services.document import IngestError, analyse, ingest

SAMPLES = Path(__file__).resolve().parents[1] / "sample_invoices"


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


# --- ingest ----------------------------------------------------------------
def test_a_real_pdf_is_accepted_and_hashed():
    digest, size = ingest(sample("00_happy_path_bharat_steel.pdf"))
    assert len(digest) == 64
    assert size > 1000


def test_the_same_bytes_always_hash_the_same():
    """Duplicate-by-hash detection depends on this."""
    data = sample("00_happy_path_bharat_steel.pdf")
    assert ingest(data)[0] == ingest(data)[0]


def test_different_invoices_hash_differently():
    a = ingest(sample("00_happy_path_bharat_steel.pdf"))[0]
    b = ingest(sample("03_duplicate_vega.pdf"))[0]
    assert a != b


def test_an_empty_file_is_refused_clearly():
    with pytest.raises(IngestError) as exc:
        ingest(b"")
    assert exc.value.code == "FILE_EMPTY"


@pytest.mark.parametrize("data,filename,label", [
    (b"\x89PNG\r\n\x1a\n" + b"0" * 100, "scan.png", "PNG image"),
    (b"\xff\xd8\xff" + b"0" * 100, "photo.jpg", "JPEG image"),
    (b"PK\x03\x04" + b"0" * 100, "invoice.docx", "ZIP / Office document"),
    # No signature and no extension: there is nothing left to go on.
    (b"just some text here", "attachment", "unrecognised file type"),
])
def test_non_pdf_uploads_are_refused_and_named(data, filename, label):
    with pytest.raises(IngestError) as exc:
        ingest(data, filename)
    assert exc.value.code == "FILE_NOT_PDF"
    assert exc.value.evidence["detected"] == label


def test_an_unknown_signature_falls_back_to_the_file_extension():
    """'.bin file' tells a user more than 'unrecognised file type'."""
    with pytest.raises(IngestError) as exc:
        ingest(b"mystery bytes", "thing.bin")
    assert exc.value.evidence["detected"] == ".bin file"


def test_a_truncated_pdf_is_reported_as_corrupt(rules):
    broken = sample("00_happy_path_bharat_steel.pdf")[:400]
    with pytest.raises(IngestError) as exc:
        analyse(broken, rules)
    assert exc.value.code == "FILE_CORRUPT"
    assert exc.value.evidence.get("detail")


# --- analysis --------------------------------------------------------------
def test_a_text_pdf_takes_the_text_path(rules):
    info, findings = analyse(sample("00_happy_path_bharat_steel.pdf"), rules)
    assert info.is_scanned is False
    assert info.page_images == [], "text PDFs must not be rendered needlessly"
    assert "Bharat Steel" in info.text
    assert findings == []


def test_a_scanned_pdf_takes_the_vision_path(rules):
    info, findings = analyse(sample("01_scanned_no_po_deccan.pdf"), rules)
    assert info.is_scanned is True
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
