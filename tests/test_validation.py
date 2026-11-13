"""Normalisation and arithmetic validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.rules.validation import (gstin_problem, normalise_invoice_number,
                                          normalise_po_reference, parse_date,
                                          parse_money, validate_and_normalise)

from .conftest import make_extraction

D = Decimal


def codes(findings):
    return {f.code for f in findings}


# --- invoice number normalisation -----------------------------------------
@pytest.mark.parametrize("a,b", [
    ("INV-2026-0042", "INV/2026/42"),
    ("INV 2026 42", "inv-2026-0042"),
    ("BSI-2026-1042", "BSI/2026/1042"),
    ("INV0042", "INV-42"),
])
def test_cosmetically_different_numbers_collapse_to_one_key(a, b):
    assert normalise_invoice_number(a) == normalise_invoice_number(b)


@pytest.mark.parametrize("a,b", [
    ("INV-2026-0042", "INV-2026-0043"),
    ("INV-2026-42", "INV-2025-42"),
    ("BSI-2026-1042", "NPL-2026-1042"),
])
def test_genuinely_different_numbers_stay_different(a, b):
    assert normalise_invoice_number(a) != normalise_invoice_number(b)


def test_missing_invoice_number_normalises_to_none():
    assert normalise_invoice_number(None) is None
    assert normalise_invoice_number("   ") is None


# --- PO reference normalisation -------------------------------------------
@pytest.mark.parametrize("raw", [
    "PO-4501", "po 4501", "P.O. #4501", "PO4501", "Our Ref: PO-4501", "  po-4501 ",
])
def test_po_reference_variants_normalise_together(raw):
    assert normalise_po_reference(raw) == "PO4501"


def test_po_reference_distinguishes_different_numbers():
    assert normalise_po_reference("PO-4501") != normalise_po_reference("PO-4502")


# --- dates and money -------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2026-11-04", date(2026, 11, 4)),
    ("04-11-2026", date(2026, 11, 4)),
    ("04/11/2026", date(2026, 11, 4)),
    ("4 Nov 2026", date(2026, 11, 4)),
    ("04-Nov-2026", date(2026, 11, 4)),
])
def test_date_formats_vendors_actually_use(raw, expected):
    assert parse_date(raw) == expected


def test_unparseable_date_returns_none_rather_than_guessing():
    assert parse_date("sometime in November") is None


@pytest.mark.parametrize("raw,expected", [
    ("1,07,026.00", D("107026.00")),
    ("INR 1,07,026.00", D("107026.00")),
    ("107026", D("107026.00")),
    ("₹ 72,570.00", D("72570.00")),
    (107026.0, D("107026.00")),
])
def test_money_parsing_handles_indian_grouping_and_symbols(raw, expected):
    assert parse_money(raw) == expected


def test_money_parsing_refuses_non_numbers():
    assert parse_money("not a number") is None
    assert parse_money("") is None
    assert parse_money(None) is None


# --- GSTIN -----------------------------------------------------------------
def test_seeded_gstins_pass_their_own_checksum(vendors):
    for vendor in vendors:
        assert gstin_problem(vendor.gstin) is None, vendor.gstin


def test_gstin_with_wrong_check_digit_is_rejected():
    assert gstin_problem("27AABCB1234D1ZZ") == "check digit is wrong"


def test_gstin_of_wrong_length_is_rejected():
    assert "15 characters" in gstin_problem("27AABCB1234D1")


# --- the stage -------------------------------------------------------------
def test_clean_invoice_produces_no_validation_problems(rules):
    inv, findings = validate_and_normalise(make_extraction(), rules)
    assert codes(findings) == set()
    assert inv.total == D("107026.00")
    assert inv.invoice_date == date(2026, 11, 4)
    assert inv.po_reference_normalised == "PO4501"


def test_missing_critical_fields_are_each_reported(rules):
    _, findings = validate_and_normalise(
        make_extraction(invoice_number=None, invoice_date=None, total=None), rules)
    assert {"MISSING_INVOICE_NUMBER", "MISSING_INVOICE_DATE",
            "MISSING_TOTAL"} <= codes(findings)


def test_lines_not_summing_to_subtotal_is_caught(rules):
    _, findings = validate_and_normalise(make_extraction(subtotal=D("90000.00")), rules)
    assert "ARITHMETIC_LINES_VS_SUBTOTAL" in codes(findings)


def test_subtotal_plus_tax_not_equalling_total_is_caught(rules):
    _, findings = validate_and_normalise(make_extraction(total=D("110000.00")), rules)
    assert "ARITHMETIC_SUBTOTAL_TAX_TOTAL" in codes(findings)


def test_one_rupee_of_rounding_is_tolerated(rules):
    _, findings = validate_and_normalise(make_extraction(total=D("107026.50")), rules)
    assert "ARITHMETIC_SUBTOTAL_TAX_TOTAL" not in codes(findings)


def test_tax_inclusive_invoice_back_computes_the_taxable_value(rules):
    """The Suryodaya sample: 72,570 inclusive of 18% GST."""
    inv, findings = validate_and_normalise(make_extraction(
        tax_inclusive=True, subtotal=None, tax_total=None,
        total=D("72570.00"), tax_rate_pct=D("18"),
    ), rules)
    assert "TAX_INCLUSIVE_PRESENTATION" in codes(findings)
    assert inv.subtotal == D("61500.00")
    assert inv.tax_total == D("11070.00")
    # Back-computing must not then trip the arithmetic checks.
    assert "ARITHMETIC_SUBTOTAL_TAX_TOTAL" not in codes(findings)


def test_low_model_confidence_on_a_critical_field_is_escalated(rules):
    _, findings = validate_and_normalise(make_extraction(
        field_confidence={"vendor_name": 0.97, "invoice_number": 0.41,
                          "invoice_date": 0.97, "total": 0.99}), rules)
    assert "LOW_CONFIDENCE_CRITICAL_FIELD" in codes(findings)


def test_absent_field_is_reported_as_missing_not_as_low_confidence(rules):
    """A field that is not on the document is one problem, not two."""
    _, findings = validate_and_normalise(make_extraction(invoice_number=None), rules)
    assert "MISSING_INVOICE_NUMBER" in codes(findings)
    assert "LOW_CONFIDENCE_CRITICAL_FIELD" not in codes(findings)


def test_confidence_is_capped_by_evidence_not_just_self_reported(rules):
    """A model claiming 0.99 on numbers that do not add up must be doubted."""
    inv, _ = validate_and_normalise(make_extraction(total=D("110000.00")), rules)
    assert inv.confidence["total"] <= 0.6


def test_unparseable_date_caps_its_own_confidence(rules):
    inv, _ = validate_and_normalise(
        make_extraction(invoice_date="whenever"), rules)
    assert inv.confidence["invoice_date"] == 0.0
