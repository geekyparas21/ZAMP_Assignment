"""Shared fixtures. No network access anywhere in this suite."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.config import get_rules
from backend.app.domain import ExtractedInvoice, ExtractedLine
from backend.app.reference import load_ledger, load_purchase_orders, load_vendors

D = Decimal


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
