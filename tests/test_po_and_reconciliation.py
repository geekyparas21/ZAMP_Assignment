"""PO matching, inference, and reconciliation against tolerances."""

from __future__ import annotations

from decimal import Decimal

from backend.app.domain import ExtractedLine
from backend.app.rules.po_matching import match_po
from backend.app.rules.reconciliation import reconcile
from backend.app.rules.validation import validate_and_normalise

from .conftest import make_extraction

D = Decimal


def codes(findings):
    return {f.code for f in findings}


def normalised(rules, **overrides):
    inv, _ = validate_and_normalise(make_extraction(**overrides), rules)
    return inv


def evidence_for(findings, code):
    return next(f for f in findings if f.code == code).evidence


# ===========================================================================
# PO matching
# ===========================================================================
def test_explicit_po_reference_is_used(rules, vendors, purchase_orders):
    vendor = next(v for v in vendors if v.vendor_id == "V001")
    match, findings = match_po(normalised(rules), vendor, purchase_orders, rules)
    assert match.po.po_number == "PO-4501"
    assert match.method == "explicit"
    assert "PO_MATCHED_EXPLICIT" in codes(findings)


def test_oddly_formatted_po_reference_still_resolves(rules, vendors, purchase_orders):
    vendor = next(v for v in vendors if v.vendor_id == "V001")
    match, _ = match_po(normalised(rules, po_reference="Our Ref: P.O. #4501"),
                        vendor, purchase_orders, rules)
    assert match.po.po_number == "PO-4501"


def test_closed_po_is_flagged(rules, vendors, purchase_orders):
    vendor = next(v for v in vendors if v.vendor_id == "V008")
    _, findings = match_po(
        normalised(rules, vendor_name="Vega Instruments Pvt Ltd",
                   vendor_gstin="19AAICV0123L1Z7", po_reference="PO-4540",
                   total=D("94400.00"), subtotal=D("80000.00"),
                   tax_total=D("14400.00")),
        vendor, purchase_orders, rules)
    assert "PO_CLOSED" in codes(findings)


def test_unique_open_po_is_inferred_confidently(rules, vendors, purchase_orders):
    """Scenario 1: the scanned Deccan invoice quotes no PO."""
    vendor = next(v for v in vendors if v.vendor_id == "V004")
    inv = normalised(
        rules, vendor_name="Deccan Precision Tools Pvt. Ltd.",
        vendor_gstin="36AAECD3456G1ZQ", po_reference=None,
        invoice_number="DPT/2026/0247", bank_account_last4="5533",
        lines=[
            ExtractedLine("Carbide turning insert CNMG120408", "8209", D("500"),
                          "PCS", D("212.00"), D("106000.00")),
            ExtractedLine("Tool holder PCLNR2525M12", "8466", D("10"), "NOS",
                          D("3450.00"), D("34500.00")),
        ],
        subtotal=D("140500.00"), tax_total=D("25290.00"), total=D("165790.00"))
    match, findings = match_po(inv, vendor, purchase_orders, rules)
    assert match.po.po_number == "PO-4520"
    assert match.method == "inferred"
    assert "PO_INFERRED_HIGH_CONFIDENCE" in codes(findings)


def test_two_similar_open_pos_make_inference_ambiguous(rules, vendors, purchase_orders):
    """Scenario 1b: Sunrise has two near-identical open POs."""
    vendor = next(v for v in vendors if v.vendor_id == "V005")
    inv = normalised(
        rules, vendor_name="Sunrise Packaging Co.", vendor_gstin="24AAFCS7890H1ZZ",
        po_reference=None, invoice_number="SPC-2026-0417",
        bank_account_last4="9021",
        lines=[ExtractedLine("Corrugated box 5-ply 600x400x300mm", "4819",
                             D("3020"), "NOS", D("46.00"), D("138920.00"))],
        subtotal=D("138920.00"), tax_total=D("25005.60"), total=D("163925.60"))
    match, findings = match_po(inv, vendor, purchase_orders, rules)
    assert match.po is None, "an ambiguous match must never be silently chosen"
    assert "PO_INFERRED_AMBIGUOUS" in codes(findings)
    # The candidates must be shown, not hidden.
    assert len(match.candidates) >= 2


def test_inference_offers_candidates_rather_than_failing_blind(
        rules, vendors, purchase_orders):
    vendor = next(v for v in vendors if v.vendor_id == "V005")
    inv = normalised(rules, vendor_name="Sunrise Packaging Co.",
                     vendor_gstin="24AAFCS7890H1ZZ", po_reference=None,
                     bank_account_last4="9021")
    match, _ = match_po(inv, vendor, purchase_orders, rules)
    assert match.candidates, "a human needs candidates to choose from"
    assert all(0.0 <= c.score <= 1.0 for c in match.candidates)


def test_unknown_po_reference_with_no_candidates_is_reported(
        rules, vendors, purchase_orders):
    vendor = next(v for v in vendors if v.vendor_id == "V006")   # blocked, PO-4570
    inv = normalised(rules, vendor_name="Orient Electricals Ltd",
                     vendor_gstin="07AAGCO2345J1ZD", po_reference="PO-9999",
                     bank_account_last4="1187", total=D("388928.00"),
                     subtotal=D("329600.00"), tax_total=D("59328.00"))
    match, findings = match_po(inv, vendor, purchase_orders, rules)
    # Either it finds the vendor's one open PO by inference, or it says it
    # could not - what it must never do is match PO-9999 to nothing silently.
    assert match.method in ("inferred", "none")
    if match.po is None:
        assert codes(findings) & {"PO_NOT_FOUND", "PO_INFERRED_LOW_CONFIDENCE",
                                  "PO_INFERRED_AMBIGUOUS"}


# ===========================================================================
# Reconciliation
# ===========================================================================
def test_exact_match_reconciles_with_no_findings(rules, po_by_number):
    recon, findings = reconcile(normalised(rules), po_by_number["PO-4501"], rules)
    assert codes(findings) == set()
    assert recon.within_tolerance is True
    assert recon.difference == D("0.00")
    assert all(c.status == "ok" for c in recon.comparisons)


def test_small_overage_is_inside_tolerance(rules, po_by_number):
    """Scenario 4a: Rs 180 of freight on a 1,35,700 PO."""
    inv = normalised(
        rules, vendor_name="Trident Fasteners & Hardware",
        vendor_gstin="23AAJCT4567M1ZT", po_reference="PO-4530",
        bank_account_last4="8809",
        lines=[
            ExtractedLine("Hex bolt M12x50 Gr.8.8 zinc plated", "7318", D("10000"),
                          "NOS", D("11.50"), D("115000.00")),
            ExtractedLine("Freight & handling charges", None, None, None, None,
                          D("152.54")),
        ],
        subtotal=D("115152.54"), tax_total=D("20727.46"), total=D("135880.00"))
    recon, findings = reconcile(inv, po_by_number["PO-4530"], rules)
    assert "TOTAL_VARIANCE_WITHIN_TOLERANCE" in codes(findings)
    assert "TOTAL_VARIANCE_EXCEEDS_TOLERANCE" not in codes(findings)
    assert recon.difference == D("180.00")


def test_incidental_freight_line_does_not_force_a_review(rules, po_by_number):
    """The Rs 152 freight line is not on the PO, but is immaterial."""
    inv = normalised(
        rules, vendor_name="Trident Fasteners & Hardware",
        vendor_gstin="23AAJCT4567M1ZT", po_reference="PO-4530",
        bank_account_last4="8809",
        lines=[
            ExtractedLine("Hex bolt M12x50 Gr.8.8 zinc plated", "7318", D("10000"),
                          "NOS", D("11.50"), D("115000.00")),
            ExtractedLine("Freight & handling charges", None, None, None, None,
                          D("152.54")),
        ],
        subtotal=D("115152.54"), tax_total=D("20727.46"), total=D("135880.00"))
    _, findings = reconcile(inv, po_by_number["PO-4530"], rules)
    assert "LINE_NOT_ON_PO" not in codes(findings)


def test_material_unmatched_line_is_escalated(rules, po_by_number):
    inv = normalised(
        rules, vendor_name="Trident Fasteners & Hardware",
        vendor_gstin="23AAJCT4567M1ZT", po_reference="PO-4530",
        bank_account_last4="8809",
        lines=[
            ExtractedLine("Hex bolt M12x50 Gr.8.8 zinc plated", "7318", D("10000"),
                          "NOS", D("11.50"), D("115000.00")),
            ExtractedLine("Consultancy retainer", None, None, None, None,
                          D("40000.00")),
        ],
        subtotal=D("155000.00"), tax_total=D("27900.00"), total=D("182900.00"))
    _, findings = reconcile(inv, po_by_number["PO-4530"], rules)
    assert "LINE_NOT_ON_PO" in codes(findings)


def test_eight_percent_price_rise_breaches_the_line_tolerance(rules, po_by_number):
    """Scenario 4b: the rate goes from 8,500 to 9,180 per trip."""
    inv = normalised(
        rules, vendor_name="Kaveri Logistics Services",
        vendor_gstin="33AADCK9012F1ZW", po_reference="PO-4531",
        bank_account_last4="2290",
        lines=[ExtractedLine("Road freight Pune to Chennai, per trip", "9965",
                             D("20"), "TRIP", D("9180.00"), D("183600.00"))],
        subtotal=D("183600.00"), tax_total=D("33048.00"), total=D("216648.00"))
    _, findings = reconcile(inv, po_by_number["PO-4531"], rules)
    assert "PRICE_VARIANCE_EXCEEDS_TOLERANCE" in codes(findings)
    detail = evidence_for(findings, "PRICE_VARIANCE_EXCEEDS_TOLERANCE")["detail"]
    assert "8,500.00" in detail and "9,180.00" in detail, \
        "the evidence must show the numbers that were compared"


def test_a_four_percent_price_rise_stays_inside_the_line_tolerance(rules, po_by_number):
    inv = normalised(
        rules, vendor_name="Kaveri Logistics Services",
        vendor_gstin="33AADCK9012F1ZW", po_reference="PO-4531",
        bank_account_last4="2290",
        lines=[ExtractedLine("Road freight Pune to Chennai, per trip", "9965",
                             D("20"), "TRIP", D("8840.00"), D("176800.00"))],
        subtotal=D("176800.00"), tax_total=D("31824.00"), total=D("208624.00"))
    _, findings = reconcile(inv, po_by_number["PO-4531"], rules)
    assert "PRICE_VARIANCE_EXCEEDS_TOLERANCE" not in codes(findings)


def test_partial_billing_is_noted_not_penalised(rules, po_by_number):
    """Scenario 2, invoice 1 of 3."""
    inv = _nova_invoice(rules, D("2000"))
    _, findings = reconcile(inv, po_by_number["PO-4510"], rules)
    assert "PARTIAL_INVOICE_AGAINST_PO" in codes(findings)
    assert "PO_OVERBILLED" not in codes(findings)


def test_third_invoice_exceeding_the_remaining_balance_is_caught(rules, po_by_number):
    """Scenario 2, invoice 3 of 3, after two part-bills have been posted."""
    po = po_by_number["PO-4510"]
    po.amount_invoiced_to_date = D("381140.00")          # two invoices posted
    try:
        inv = _nova_invoice(rules, D("1500"))
        _, findings = reconcile(inv, po, rules)
        assert "PO_OVERBILLED" in codes(findings)
        ev = evidence_for(findings, "PO_OVERBILLED")
        assert ev["remaining"] == "1,20,360.00"
        assert ev["overage"] == "30,090.00"
    finally:
        po.amount_invoiced_to_date = D("0.00")


def test_overbilling_evidence_names_every_number_a_human_needs(rules, po_by_number):
    po = po_by_number["PO-4510"]
    po.amount_invoiced_to_date = D("381140.00")
    try:
        _, findings = reconcile(_nova_invoice(rules, D("1500")), po, rules)
        ev = evidence_for(findings, "PO_OVERBILLED")
        assert {"po_total", "invoiced", "remaining", "invoice_total",
                "overage"} <= set(ev)
    finally:
        po.amount_invoiced_to_date = D("0.00")


def test_no_po_means_no_reconciliation_rather_than_a_crash(rules):
    recon, findings = reconcile(normalised(rules), None, rules)
    assert findings == []
    assert recon.comparisons == []


def _nova_invoice(rules, qty: Decimal):
    amount = (qty * D("85.00")).quantize(D("0.01"))
    tax = (amount * D("0.18")).quantize(D("0.01"))
    return normalised(
        rules, vendor_name="Nova Polymers Pvt Ltd", vendor_gstin="29AACCN5678E1Z5",
        po_reference="PO-4510", bank_account_last4="7781",
        invoice_number="NPL-2026-0712", invoice_date="2026-11-02",
        lines=[ExtractedLine("Polypropylene granules PP-H110", "3902", qty, "KG",
                             D("85.00"), amount)],
        subtotal=amount, tax_total=tax, total=(amount + tax).quantize(D("0.01")))
