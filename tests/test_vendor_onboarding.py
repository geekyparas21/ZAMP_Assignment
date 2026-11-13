"""Unknown vendors, the bill-to check, and onboarding from the screen.

The behaviour under test is a policy change made after a real-world invoice
(a consumer grocery receipt) was REJECTED for being from a supplier we had
never paid. Holding it was right; refusing it was not. In an AP team an
unknown vendor goes to procurement for onboarding - only a *blocked* vendor
or a *confirmed* duplicate is a refusal.

Everything here runs offline: extraction is either a recorded fixture or a
hand-built ExtractedInvoice, and the API tests drive a temporary SQLite file.
"""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal

import pytest

from backend.app.domain import (ExtractedInvoice, ExtractedLine, PurchaseOrder,
                                Vendor)
from backend.app.rules.evaluate import ReferenceData, evaluate
from backend.app.rules.vendor import check_bill_to, match_vendor
from backend.app.rules.validation import validate_and_normalise

D = Decimal


@pytest.fixture
def reference(vendors, purchase_orders, ledger):
    return ReferenceData(copy.deepcopy(vendors), copy.deepcopy(purchase_orders),
                         copy.deepcopy(ledger))


def codes(result):
    return {f.code for f in result.findings}


# ===========================================================================
# A consumer-style receipt from an unknown seller, billed to an individual
# ===========================================================================
def consumer_receipt(**overrides) -> ExtractedInvoice:
    """Shaped on the real Blinkit receipt that prompted this change.

    A quick-commerce grocery bill: a real GST invoice from a real company,
    addressed to a private individual, with no PO and nothing on our master.
    """
    defaults = dict(
        is_invoice=True, document_type="invoice",
        vendor_name="BLINK COMMERCE PRIVATE LIMITED",
        vendor_gstin="07AAFCG9846E2ZC",
        bill_to_name="Paras Kansal",
        bill_to_gstin=None,
        invoice_number="C387401T27012262", invoice_date="2026-10-01",
        po_reference=None, currency="INR",
        lines=[
            ExtractedLine("Real Fruit Power Orange Juice (Tetra Pak)", "22029920",
                          D("3"), None, None, D("345.00")),
            ExtractedLine("Yu Lychee Juice with Chia Seeds", "22029920",
                          D("1"), None, None, D("149.00")),
            ExtractedLine("Handling charge", None, None, None, None, D("12.00")),
        ],
        subtotal=D("481.90"), tax_total=D("24.10"), total=D("506.00"),
        tax_rate_pct=D("5"),
        field_confidence={"vendor_name": 1.0, "vendor_gstin": 1.0,
                          "invoice_number": 1.0, "invoice_date": 1.0,
                          "subtotal": 0.9, "tax_total": 0.9, "total": 0.9},
    )
    defaults.update(overrides)
    return ExtractedInvoice(**defaults)


def test_consumer_receipt_is_held_for_review_not_rejected(reference):
    """The headline case. Holding it is right; refusing it was not."""
    result = evaluate(consumer_receipt(), reference, "hash-consumer")

    assert result.outcome == "NEEDS_REVIEW"
    assert "VENDOR_UNKNOWN" in codes(result)
    assert "BILLED_TO_OTHER_ENTITY" in codes(result)
    assert "VENDOR_BLOCKED" not in codes(result)


def test_the_primary_reason_is_the_unknown_vendor(reference):
    """Several checks fire at once; identity is the one to act on first."""
    result = evaluate(consumer_receipt(), reference, "hash-consumer")
    assert result.decision.top_reason == "VENDOR_UNKNOWN"


def test_po_matching_still_runs_and_reports_that_there_is_no_po(reference):
    """An unknown vendor must not stop later stages from reporting.

    The stage runs, finds nothing, and says so - rather than being skipped and
    leaving a reviewer unsure whether a PO was ever looked for.
    """
    result = evaluate(consumer_receipt(), reference, "hash-consumer")

    assert "NO_PO_FOUND" in codes(result)
    assert "po_matching" in result.stage_findings
    finding = next(f for f in result.findings if f.code == "NO_PO_FOUND")
    assert finding.evidence["vendor_known"] is False
    assert "quotes no purchase order" in finding.message


def test_every_finding_is_listed_in_severity_order(reference):
    """The card shows all of them, worst first - not just the headline."""
    result = evaluate(consumer_receipt(), reference, "hash-consumer")
    severities = [f.severity for f in result.decision.findings]

    assert severities == sorted(severities, reverse=True)
    assert len(result.decision.findings) >= 4
    # And each one must carry the evidence that made it fire.
    for finding in result.decision.findings:
        assert finding.message.strip()
        assert finding.next_action.strip()
        assert finding.stage


def test_the_draft_email_asks_procurement_to_onboard_the_vendor(reference):
    """The next action is a specific request, with everything needed to act."""
    result = evaluate(consumer_receipt(), reference, "hash-consumer")
    email = result.decision.draft_email

    assert email["to"].startswith("procurement@")
    assert "onboarding" in email["subject"].lower()
    for expected in ("BLINK COMMERCE PRIVATE LIMITED",   # extracted name
                     "07AAFCG9846E2ZC",                  # extracted GSTIN
                     "C387401T27012262",                 # invoice number
                     "2026-10-01",                       # invoice date
                     "506.00"):                          # amount
        assert expected in email["body"], expected


def test_next_action_routes_to_procurement(reference):
    result = evaluate(consumer_receipt(), reference, "hash-consumer")
    assert "procurement" in result.decision.next_action.lower()
    assert "onboarding" in result.decision.next_action.lower()


# ===========================================================================
# The bill-to check in isolation
# ===========================================================================
def normalised(rules, **overrides):
    inv, _ = validate_and_normalise(consumer_receipt(**overrides), rules)
    return inv


def test_an_invoice_addressed_to_us_raises_nothing(rules):
    findings = check_bill_to(
        normalised(rules, bill_to_name="Acme Manufacturing Pvt Ltd"), rules)
    assert findings == []


def test_our_name_in_a_shorter_form_is_still_us(rules):
    """Vendors print our name loosely; that is not a different company."""
    findings = check_bill_to(
        normalised(rules, bill_to_name="Acme Manufacturing"), rules)
    assert findings == []


def test_our_gstin_is_enough_even_when_the_name_is_odd(rules):
    """An identifier beats a description, here as in vendor matching."""
    findings = check_bill_to(
        normalised(rules, bill_to_name="ACME MFG (PUNE PLANT)",
                   bill_to_gstin="27AAACA1111A1ZS"), rules)
    assert findings == []


def test_an_individual_as_the_buyer_is_flagged(rules):
    findings = check_bill_to(normalised(rules, bill_to_name="Paras Kansal"), rules)
    assert [f.code for f in findings] == ["BILLED_TO_OTHER_ENTITY"]
    assert "Paras Kansal" in findings[0].message


def test_another_company_as_the_buyer_is_flagged(rules):
    findings = check_bill_to(
        normalised(rules, bill_to_name="Globex Trading LLP",
                   bill_to_gstin="29AACCN5678E1Z5"), rules)
    assert [f.code for f in findings] == ["BILLED_TO_OTHER_ENTITY"]
    assert "29AACCN5678E1Z5" in findings[0].message


def test_a_document_naming_no_buyer_raises_nothing(rules):
    """Absence of evidence is not evidence of a different buyer.

    Every sample fixture recorded before this check existed has no bill-to at
    all, and none of them should start being held because of it.
    """
    findings = check_bill_to(
        normalised(rules, bill_to_name=None, bill_to_gstin=None), rules)
    assert findings == []


# ===========================================================================
# Blocked and pending vendors are unchanged
# ===========================================================================
def blocked_vendor_invoice() -> ExtractedInvoice:
    return ExtractedInvoice(
        is_invoice=True, vendor_name="Orient Electricals Ltd",
        vendor_gstin="07AAGCO2345J1ZD",
        bill_to_name="Acme Manufacturing Pvt Ltd",
        bill_to_gstin="27AAACA1111A1ZS",
        invoice_number="OEL-2026-0901", invoice_date="2026-11-06",
        po_reference="PO-4570", currency="INR", bank_account_last4="1187",
        lines=[ExtractedLine("4-core armoured cable 25sqmm", "8544", D("800"),
                             "MTR", D("412.00"), D("329600.00"))],
        subtotal=D("329600.00"), tax_total=D("59328.00"), total=D("388928.00"),
        tax_rate_pct=D("18"),
        field_confidence={"vendor_name": 0.98, "vendor_gstin": 0.98,
                          "invoice_number": 0.98, "invoice_date": 0.98,
                          "subtotal": 0.98, "tax_total": 0.98, "total": 0.98},
    )


def test_a_blocked_vendor_is_still_rejected(reference):
    """The line that separates "we have not met them" from "we said no"."""
    result = evaluate(blocked_vendor_invoice(), reference, "hash-blocked")

    assert result.outcome == "REJECTED"
    assert result.decision.top_reason == "VENDOR_BLOCKED"
    assert "VENDOR_UNKNOWN" not in codes(result)


def test_a_pending_vendor_is_held_not_rejected(reference):
    result = evaluate(ExtractedInvoice(
        is_invoice=True, vendor_name="Himalaya Chemicals Pvt Ltd",
        vendor_gstin="06AAHCH6789K1ZV",
        bill_to_name="Acme Manufacturing Pvt Ltd",
        invoice_number="HCL-2026-0444", invoice_date="2026-11-09",
        po_reference=None, currency="INR", bank_account_last4="3344",
        lines=[ExtractedLine("Caustic soda flakes", "2815", D("50"), "BAG",
                             D("1000.00"), D("50000.00"))],
        subtotal=D("50000.00"), tax_total=D("9000.00"), total=D("59000.00"),
        tax_rate_pct=D("18"),
        field_confidence={"vendor_name": 0.98, "invoice_number": 0.98,
                          "invoice_date": 0.98, "total": 0.98},
    ), reference, "hash-pending")

    assert result.outcome == "NEEDS_REVIEW"
    assert "VENDOR_PENDING" in codes(result)


# ===========================================================================
# Onboarding, then re-running: the demo flow, decided twice
# ===========================================================================
def meridian_extraction() -> ExtractedInvoice:
    """The recorded Gemini response for sample 09, parsed.

    Using the real fixture rather than a hand-built object means this test
    also proves the model actually returns the bill-to fields.
    """
    from .test_extraction import extracted
    return extracted("09_unknown_vendor_meridian")


def onboard(reference: ReferenceData) -> ReferenceData:
    """What the Reference Data page's two forms do, as the engine sees it."""
    reference.vendors.append(Vendor(
        vendor_id="V011",
        legal_name="Meridian Industrial Supplies Private Limited",
        aliases=["Meridian Industrial Supplies", "Meridian Supplies"],
        gstin="27AAMCM1234N1Z5", status="approved",
        payment_terms="Net 30", bank_account_last4="6690"))
    reference.purchase_orders.append(PurchaseOrder(
        po_number="PO-4601", vendor_id="V011", issue_date=date(2026, 11, 10),
        status="open", currency="INR", total_amount=D("87320.00"),
        amount_invoiced_to_date=D("0.00"), lines=[],
        note="Raised after onboarding; cleaning consumables."))
    return reference


def test_the_model_reads_the_buyer_off_a_real_invoice():
    """Sample 09 is addressed to us, and the extraction says so."""
    extraction = meridian_extraction()
    assert extraction.bill_to_name == "Acme Manufacturing Pvt Ltd"
    assert extraction.bill_to_gstin == "27AAACA1111A1ZS"


def test_an_unknown_vendor_billed_to_us_is_held_for_onboarding(reference):
    result = evaluate(meridian_extraction(), reference, "hash-meridian")

    assert result.outcome == "NEEDS_REVIEW"
    assert result.decision.top_reason == "VENDOR_UNKNOWN"
    assert "NO_PO_FOUND" in codes(result)
    # It is addressed to us, so the bill-to check must stay quiet.
    assert "BILLED_TO_OTHER_ENTITY" not in codes(result)
    assert result.vendor is None


def test_the_same_invoice_approves_once_the_vendor_and_po_exist(reference):
    """The whole point of the change: onboarding is a step, not a dead end.

    Only the reference data differs between this and the test above - the
    extracted invoice is byte-for-byte the same recorded response, which is
    exactly what the Re-run button replays.
    """
    held = evaluate(meridian_extraction(), reference, "hash-meridian")
    assert held.outcome == "NEEDS_REVIEW"

    result = evaluate(meridian_extraction(), onboard(reference), "hash-meridian-2")

    assert result.outcome == "APPROVED"
    assert result.vendor.vendor_id == "V011"
    assert result.matched_po.po_number == "PO-4601"
    assert "VENDOR_UNKNOWN" not in codes(result)
    assert "NO_PO_FOUND" not in codes(result)
    assert "ALL_CHECKS_PASSED" in codes(result)


def test_a_value_only_po_reconciles_on_the_total_and_says_so(reference):
    """A PO raised with no line detail must not fail every invoice line.

    A blanket or service PO genuinely carries only a committed value. Marking
    each invoiced line "not on the PO" would hold the invoice for a gap in our
    own reference data, so the total is reconciled and the limitation stated.
    """
    result = evaluate(meridian_extraction(), onboard(reference), "hash-value-po")

    assert "PO_HAS_NO_LINE_DETAIL" in codes(result)
    assert "LINE_NOT_ON_PO" not in codes(result)
    finding = next(f for f in result.findings if f.code == "PO_HAS_NO_LINE_DETAIL")
    assert "PO-4601" in finding.message
    assert "only the total was reconciled" in finding.message


def test_a_value_only_po_can_still_be_inferred_confidently(reference):
    """Unknown line similarity must not be scored as zero similarity.

    Weighting absent line detail as 0.0 capped any value-only PO at 0.65,
    below the 0.75 inference floor - so a PO whose amount matched to the paisa
    could never be matched to the invoice it was raised for.
    """
    result = evaluate(meridian_extraction(), onboard(reference), "hash-value-po")

    assert "PO_INFERRED_HIGH_CONFIDENCE" in codes(result)
    inferred = next(f for f in result.findings
                    if f.code == "PO_INFERRED_HIGH_CONFIDENCE")
    assert inferred.evidence["score"] >= 0.75
    assert "no line detail" in inferred.evidence["reason"]


def test_onboarding_the_vendor_alone_still_needs_a_po(reference):
    """Each addition must change exactly what it should, and no more."""
    reference.vendors.append(Vendor(
        vendor_id="V011",
        legal_name="Meridian Industrial Supplies Private Limited",
        aliases=["Meridian Industrial Supplies"], gstin="27AAMCM1234N1Z5",
        status="approved", payment_terms="Net 30", bank_account_last4="6690"))

    result = evaluate(meridian_extraction(), reference, "hash-vendor-only")

    assert result.outcome == "NEEDS_REVIEW"
    assert "VENDOR_UNKNOWN" not in codes(result)
    assert result.decision.top_reason == "NO_PO_FOUND"
    finding = next(f for f in result.findings if f.code == "NO_PO_FOUND")
    assert finding.evidence["vendor_known"] is True
    assert "Meridian" in finding.message
