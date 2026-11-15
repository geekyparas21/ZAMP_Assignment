"""Shared fixtures. No network access anywhere in this suite."""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.config import get_rules
from backend.app.domain import ExtractedInvoice, ExtractedLine
from backend.app.reference import load_ledger, load_purchase_orders, load_vendors

D = Decimal

SAMPLES = Path(__file__).resolve().parents[1] / "sample_invoices"


# ---------------------------------------------------------------------------
# Building image uploads to test with
#
# Rendered from a real sample PDF rather than drawn from scratch, so what the
# tests feed the pipeline is a picture of an actual invoice.
# ---------------------------------------------------------------------------
def render_sample(name: str = "00_happy_path_bharat_steel.pdf", dpi: int = 110):
    """One page of a sample PDF as a PIL image."""
    import pymupdf
    from PIL import Image

    with pymupdf.open(SAMPLES / name) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def encode(image, fmt: str = "PNG", exif_orientation: int | None = None,
           **save_options) -> bytes:
    """Encode a PIL image, optionally tagging it with an EXIF orientation.

    When an orientation is asked for, the pixels are rotated the way a camera
    stores them and the tag records how they should be displayed - which is
    exactly the situation a phone photo arrives in.
    """
    from PIL import Image

    data = image
    exif = None
    if exif_orientation:
        rotations = {3: 180, 6: 90, 8: 270}
        if exif_orientation in rotations:
            data = image.rotate(rotations[exif_orientation], expand=True)
        exif = Image.Exif()
        exif[0x0112] = exif_orientation

    buffer = io.BytesIO()
    if exif is not None:
        data.save(buffer, format=fmt, exif=exif.tobytes(), **save_options)
    else:
        data.save(buffer, format=fmt, **save_options)
    return buffer.getvalue()


def image_upload(fmt: str = "PNG", size: tuple[int, int] | None = None,
                 exif_orientation: int | None = None,
                 sample: str = "00_happy_path_bharat_steel.pdf") -> bytes:
    image = render_sample(sample)
    if size:
        image = image.resize(size)
    return encode(image, fmt, exif_orientation)


# ---------------------------------------------------------------------------
# A temporary database, for the tests that run the whole pipeline
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from backend.app import db as db_module
    from backend.app.config import get_settings

    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    db_module.reset_engine()
    db_module.init_db()
    yield db_module
    db_module.reset_engine()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def rules():
    return get_rules()


@pytest.fixture(scope="session")
def vendors():
    return load_vendors()


@pytest.fixture(scope="session")
def purchase_orders():
    return load_purchase_orders()


@pytest.fixture(scope="session")
def ledger():
    return load_ledger()


@pytest.fixture
def po_by_number(purchase_orders):
    return {po.po_number: po for po in purchase_orders}


@pytest.fixture
def vendor_by_id(vendors):
    return {v.vendor_id: v for v in vendors}


def make_extraction(**overrides) -> ExtractedInvoice:
    """A clean, fully-confident extraction that every check should pass.

    Tests override one field at a time, so each assertion isolates one rule.
    """
    defaults = dict(
        is_invoice=True,
        document_type="invoice",
        vendor_name="Bharat Steel Ind.",
        vendor_gstin="27AABCB1234D1ZC",
        invoice_number="BSI-2026-1042",
        invoice_date="2026-11-04",
        due_date="2026-12-04",
        po_reference="PO-4501",
        currency="INR",
        lines=[
            ExtractedLine("MS Plate 10mm IS2062 Gr.B", "7208", D("120"), "NOS",
                          D("485.00"), D("58200.00")),
            ExtractedLine("MS Angle 50x50x5mm", "7216", D("200"), "NOS",
                          D("162.50"), D("32500.00")),
        ],
        subtotal=D("90700.00"),
        tax_total=D("16326.00"),
        total=D("107026.00"),
        tax_rate_pct=D("18"),
        tax_inclusive=False,
        bank_account_last4="4412",
        field_confidence={
            "vendor_name": 0.97, "vendor_gstin": 0.96, "invoice_number": 0.98,
            "invoice_date": 0.97, "due_date": 0.95, "po_reference": 0.98,
            "subtotal": 0.97, "tax_total": 0.96, "total": 0.99,
        },
    )
    defaults.update(overrides)
    return ExtractedInvoice(**defaults)


@pytest.fixture
def extraction():
    return make_extraction
