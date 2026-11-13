"""The five graded scenarios, end-to-end through the deterministic stages.

Extraction is supplied as a fixture here (what the model would return for each
sample PDF), so these tests pin the *decision* logic without touching the API.
Phase 4 adds the live extraction tests that feed real model output into the
same function.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from backend.app.domain import ExtractedLine
from backend.app.rules.evaluate import ReferenceData, apply_approval, evaluate

from .conftest import make_extraction

D = Decimal


@pytest.fixture
def reference(vendors, purchase_orders, ledger):
    """A fresh copy per test, so one test's approval cannot leak into another."""
    return ReferenceData(
        vendors=copy.deepcopy(vendors),
        purchase_orders=copy.deepcopy(purchase_orders),
        ledger=copy.deepcopy(ledger),
    )


def nova(qty: Decimal, number: str, invoice_date: str):
    amount = (qty * D("85.00")).quantize(D("0.01"))
    tax = (amount * D("0.18")).quantize(D("0.01"))
    return make_extraction(
        vendor_name="Nova Polymers Pvt Ltd", vendor_gstin="29AACCN5678E1Z5",
        invoice_number=number, invoice_date=invoice_date, due_date="2026-12-17",
        po_reference="PO-4510", bank_account_last4="7781",
        lines=[ExtractedLine("Polypropylene granules PP-H110", "3902", qty, "KG",
                             D("85.00"), amount)],
        subtotal=amount, tax_total=tax, total=(amount + tax).quantize(D("0.01")))


# ===========================================================================
# Scenario 0 - happy path
# ===========================================================================
def test_scenario_0_happy_path_is_approved(reference):
    result = evaluate(make_extraction(), reference, "hash-0")
    assert result.outcome == "APPROVED"
    assert result.matched_po.po_number == "PO-4501"
    assert result.vendor.vendor_id == "V001"
    assert "ALL_CHECKS_PASSED" in result.codes


def test_scenario_0_approval_moves_the_po_balance(reference):
    result = evaluate(make_extraction(), reference, "hash-0")
    effect = apply_approval(result)
    assert effect["po_number"] == "PO-4501"
    assert effect["invoiced_after"] == "1,07,026.00"
    assert effect["remaining_after"] == "0.00"


def test_scenario_0_explains_itself_without_jargon(reference):
    result = evaluate(make_extraction(), reference, "hash-0")
    assert "Approved for payment." in result.decision.explanation
    assert result.decision.next_action
    assert result.decision.draft_email is None


# ===========================================================================
# Scenario 1 - scanned invoice, PO inferred
# ===========================================================================
def deccan_extraction():
    return make_extraction(
        vendor_name="Deccan Precision Tools Pvt. Ltd.",
        vendor_gstin="36AAECD3456G1ZQ", invoice_number="DPT/2026/0247",
        invoice_date="2026-11-05", due_date="2026-12-05",
        po_reference=None, bank_account_last4="5533",
        lines=[
            ExtractedLine("Carbide turning insert CNMG120408", "8209", D("500"),
                          "PCS", D("212.00"), D("106000.00")),
            ExtractedLine("Tool holder PCLNR2525M12", "8466", D("10"), "NOS",
                          D("3450.00"), D("34500.00")),
        ],
        subtotal=D("140500.00"), tax_total=D("25290.00"), total=D("165790.00"))


def test_scenario_1_scanned_invoice_infers_its_po_and_approves(reference):
    from backend.app.rules.codes import Finding
    scanned = [Finding("SCANNED_DOCUMENT", "document_analysis",
                       {"chars": 0, "pages": 1})]
    result = evaluate(deccan_extraction(), reference, "hash-1",
                      prior_findings=scanned)
    assert result.outcome == "APPROVED"
    assert result.matched_po.po_number == "PO-4520"
    assert "PO_INFERRED_HIGH_CONFIDENCE" in result.codes
    assert "SCANNED_DOCUMENT" in result.codes


def test_scenario_1_says_out_loud_that_the_po_was_inferred(reference):
    """An inferred PO must never look like one the vendor quoted."""
    result = evaluate(deccan_extraction(), reference, "hash-1")
    inferred = next(f for f in result.findings
                    if f.code == "PO_INFERRED_HIGH_CONFIDENCE")
    assert "PO-4520" in inferred.message
    assert "quotes no PO" in inferred.message


def test_scenario_1b_two_similar_pos_go_to_a_human(reference):
    result = evaluate(make_extraction(
        vendor_name="Sunrise Packaging Co.", vendor_gstin="24AAFCS7890H1ZZ",
        invoice_number="SPC-2026-0417", invoice_date="2026-11-07",
        due_date="2026-12-07", po_reference=None, bank_account_last4="9021",
        lines=[ExtractedLine("Corrugated box 5-ply 600x400x300mm", "4819",
                             D("3020"), "NOS", D("46.00"), D("138920.00"))],
        subtotal=D("138920.00"), tax_total=D("25005.60"), total=D("163925.60"),
    ), reference, "hash-1b")
    assert result.outcome == "NEEDS_REVIEW"
    assert "PO_INFERRED_AMBIGUOUS" in result.codes
    assert result.matched_po is None
    assert "procurement@" in result.decision.draft_email["to"]


# ===========================================================================
# Scenario 2 - split PO, run sequentially
# ===========================================================================
def test_scenario_2_three_invoices_run_in_sequence(reference):
    """The first two part-bills approve; the third over-bills and is held.

    This is the stateful one: each approval moves the PO balance, and the
    third invoice is judged against what is actually left.
    """
    po = next(p for p in reference.purchase_orders if p.po_number == "PO-4510")

    first = evaluate(nova(D("2000"), "NPL-2026-0712", "2026-11-02"),
                     reference, "hash-2a")
    assert first.outcome == "APPROVED"
    assert "PARTIAL_INVOICE_AGAINST_PO" in first.codes
    po.amount_invoiced_to_date = apply_approval(first)["_raw_after"]

    second = evaluate(nova(D("2000"), "NPL-2026-0736", "2026-11-08"),
                      reference, "hash-2b")
    assert second.outcome == "APPROVED"
    po.amount_invoiced_to_date = apply_approval(second)["_raw_after"]
    assert po.remaining_balance == D("100300.00")

    third = evaluate(nova(D("1500"), "NPL-2026-0759", "2026-11-12"),
                     reference, "hash-2c")
    assert third.outcome == "NEEDS_REVIEW"
    assert "PO_OVERBILLED" in third.codes
    assert apply_approval(third) is None, "a held invoice must not move the balance"

    evidence = next(f for f in third.findings if f.code == "PO_OVERBILLED").evidence
    assert evidence["remaining"] == "1,00,300.00"
    assert evidence["overage"] == "50,150.00"


def test_scenario_2_third_invoice_would_pass_on_a_fresh_po(reference):
    """Proves the hold is caused by the running balance, not the amount."""
    result = evaluate(nova(D("1500"), "NPL-2026-0759", "2026-11-12"),
                      reference, "hash-2c-fresh")
    assert result.outcome == "APPROVED"


# ===========================================================================
# Scenario 3 - duplicates
# ===========================================================================
def test_scenario_3_reformatted_duplicate_is_rejected(reference):
    result = evaluate(make_extraction(
        vendor_name="Vega Instruments Pvt Ltd", vendor_gstin="19AAICV0123L1Z7",
        invoice_number="INV/2026/42", invoice_date="2026-10-15",
        due_date="2026-11-14", po_reference="PO-4540", bank_account_last4="6655",
        lines=[ExtractedLine("Digital pressure gauge 0-100 bar", "9026", D("40"),
                             "NOS", D("2000.00"), D("80000.00"))],
        subtotal=D("80000.00"), tax_total=D("14400.00"), total=D("94400.00"),
    ), reference, "a-different-file-hash")
    assert result.outcome == "REJECTED"
    assert result.decision.top_reason == "DUPLICATE_CONFIRMED_NUMBER"
    assert "INV-2026-0042" in result.decision.explanation


def test_scenario_3b_suspected_duplicate_is_held_not_rejected(reference):
    result = evaluate(make_extraction(
        vendor_name="Suryodaya Engg Works", vendor_gstin="08AAKCS8901N1ZP",
        invoice_number="SEW-2026-131", invoice_date="2026-11-12",
        due_date="2026-12-27", po_reference="PO-4550", bank_account_last4="4470",
        lines=[ExtractedLine("Fabricated bracket assembly type-7", "7308",
                             D("150"), "NOS", D("483.80"), D("72570.00"))],
        subtotal=None, tax_total=None, total=D("72570.00"),
        tax_inclusive=True, tax_rate_pct=D("18"),
    ), reference, "hash-3b")
    assert result.outcome == "NEEDS_REVIEW"
    assert "DUPLICATE_SUSPECTED" in result.codes
    assert "TAX_INCLUSIVE_PRESENTATION" in result.codes
    assert "DUPLICATE_CONFIRMED_NUMBER" not in result.codes
    # The GST-inclusive rate must not then trip the reconciliation.
    assert "PRICE_VARIANCE_EXCEEDS_TOLERANCE" not in result.codes


# ===========================================================================
# Scenario 4 - tolerance vs real price creep
# ===========================================================================
def test_scenario_4a_freight_within_tolerance_is_approved(reference):
    result = evaluate(make_extraction(
        vendor_name="Trident Fasteners & Hardware", vendor_gstin="23AAJCT4567M1ZT",
        invoice_number="TFH-2026-2288", invoice_date="2026-11-09",
        due_date="2026-12-09", po_reference="PO-4530", bank_account_last4="8809",
        lines=[
            ExtractedLine("Hex bolt M12x50 Gr.8.8 zinc plated", "7318", D("10000"),
                          "NOS", D("11.50"), D("115000.00")),
            ExtractedLine("Freight & handling charges", None, None, None, None,
                          D("152.54")),
        ],
        subtotal=D("115152.54"), tax_total=D("20727.46"), total=D("135880.00"),
    ), reference, "hash-4a")
    assert result.outcome == "APPROVED"
    assert "TOTAL_VARIANCE_WITHIN_TOLERANCE" in result.codes
    noted = next(f for f in result.findings
                 if f.code == "TOTAL_VARIANCE_WITHIN_TOLERANCE")
    assert "180.00" in noted.message


def test_scenario_4b_eight_percent_price_creep_is_held(reference):
    result = evaluate(make_extraction(
        vendor_name="Kaveri Logistics Services", vendor_gstin="33AADCK9012F1ZW",
        invoice_number="KLS-2026-0588", invoice_date="2026-11-10",
        due_date="2026-11-25", po_reference="PO-4531", bank_account_last4="2290",
        lines=[ExtractedLine("Road freight Pune to Chennai, per trip", "9965",
                             D("20"), "TRIP", D("9180.00"), D("183600.00"))],
        subtotal=D("183600.00"), tax_total=D("33048.00"), total=D("216648.00"),
    ), reference, "hash-4b")
    assert result.outcome == "NEEDS_REVIEW"
    assert result.decision.top_reason == "PRICE_VARIANCE_EXCEEDS_TOLERANCE"
    email = result.decision.draft_email
    assert "9,180.00" in email["body"] and "8,500.00" in email["body"]
    assert "credit note" in email["body"]


# ===========================================================================
# Robustness - must be handled, must not crash
# ===========================================================================
def test_a_quotation_is_rejected_as_not_an_invoice(reference):
    result = evaluate(make_extraction(is_invoice=False, document_type="quotation"),
                      reference, "hash-q")
    assert result.outcome == "REJECTED"
    assert "NOT_AN_INVOICE" in result.codes


def test_blocked_vendor_is_rejected(reference):
    result = evaluate(make_extraction(
        vendor_name="Orient Electricals Ltd", vendor_gstin="07AAGCO2345J1ZD",
        invoice_number="OEL-2026-0901", invoice_date="2026-11-06",
        po_reference="PO-4570", bank_account_last4="1187",
        lines=[ExtractedLine("4-core armoured cable 25sqmm", "8544", D("800"),
                             "MTR", D("412.00"), D("329600.00"))],
        subtotal=D("329600.00"), tax_total=D("59328.00"), total=D("388928.00"),
    ), reference, "hash-5")
    assert result.outcome == "REJECTED"
    assert "VENDOR_BLOCKED" in result.codes


def test_missing_invoice_number_is_held(reference):
    result = evaluate(make_extraction(
        invoice_number=None, po_reference="PO-4590",
        lines=[ExtractedLine("MS Round bar 20mm", "7214", D("500"), "NOS",
                             D("92.00"), D("46000.00"))],
        subtotal=D("46000.00"), tax_total=D("8280.00"), total=D("54280.00"),
    ), reference, "hash-7")
    assert result.outcome == "NEEDS_REVIEW"
    assert "MISSING_INVOICE_NUMBER" in result.codes


def test_bank_detail_change_is_held_with_a_fraud_warning(reference):
    result = evaluate(make_extraction(
        invoice_number="BSI-2026-1077", invoice_date="2026-11-11",
        po_reference="PO-4590", bank_account_last4="9931",
        lines=[ExtractedLine("MS Round bar 20mm", "7214", D("500"), "NOS",
                             D("92.00"), D("46000.00"))],
        subtotal=D("46000.00"), tax_total=D("8280.00"), total=D("54280.00"),
    ), reference, "hash-8")
    assert result.outcome == "NEEDS_REVIEW"
    assert "VENDOR_BANK_MISMATCH" in result.codes
    assert "do not pay" in result.decision.next_action.lower()


def test_an_entirely_empty_extraction_does_not_crash(reference):
    from backend.app.domain import ExtractedInvoice
    result = evaluate(ExtractedInvoice(), reference, "hash-empty")
    assert result.outcome in ("NEEDS_REVIEW", "REJECTED")
    assert result.decision.explanation


def test_every_outcome_carries_an_explanation_and_an_action(reference):
    cases = [
        make_extraction(),
        make_extraction(invoice_number=None),
        make_extraction(is_invoice=False, document_type="receipt"),
    ]
    for extraction in cases:
        result = evaluate(extraction, reference, "hash-x")
        assert result.decision.explanation.strip(), result.outcome
        assert result.decision.next_action.strip(), result.outcome
