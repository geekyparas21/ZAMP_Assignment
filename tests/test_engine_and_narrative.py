"""The decision engine's precedence logic, and the generated output."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.rules.codes import (Finding, Severity, all_codes, get,
                                     sort_findings)
from backend.app.rules.engine import blocking_findings, decide
from backend.app.rules.narrative import (build_draft_email, build_explanation,
                                         build_next_action)
from backend.app.rules.validation import validate_and_normalise

from .conftest import make_extraction

D = Decimal


def f(code, **evidence):
    return Finding(code, "test", evidence)


# ===========================================================================
# Precedence
# ===========================================================================
def test_only_info_findings_approve():
    decision = decide([f("VENDOR_MATCHED_BY_GSTIN", gstin="X", vendor_name="Y",
                         vendor_id="V001")])
    assert decision.outcome == "APPROVED"


def test_a_review_finding_holds_the_invoice():
    decision = decide([
        f("VENDOR_MATCHED_BY_GSTIN", gstin="X", vendor_name="Y", vendor_id="V001"),
        f("MISSING_INVOICE_NUMBER"),
    ])
    assert decision.outcome == "NEEDS_REVIEW"


def test_a_reject_finding_beats_any_number_of_reviews():
    decision = decide([
        f("MISSING_INVOICE_NUMBER"),
        f("VENDOR_BANK_MISMATCH", invoice_last4="1", master_last4="2", vendor_name="Y"),
        f("VENDOR_BLOCKED", vendor_name="Y", vendor_id="V006", status="blocked"),
    ])
    assert decision.outcome == "REJECTED"


def test_a_processing_failure_beats_a_policy_rejection():
    """A document we could not read is an open question, not a judgement."""
    decision = decide([
        f("VENDOR_BLOCKED", vendor_name="Y", vendor_id="V006", status="blocked"),
        f("LLM_RATE_LIMITED", attempts=3),
    ])
    assert decision.outcome == "FAILED"


def test_no_findings_at_all_is_a_failure_not_an_approval():
    """The most dangerous possible bug: approving because nothing ran."""
    decision = decide([])
    assert decision.outcome == "FAILED"


def test_approval_still_carries_a_stated_reason():
    decision = decide([f("VENDOR_MATCHED_BY_GSTIN", gstin="X", vendor_name="Y",
                         vendor_id="V001")])
    assert any(x.code == "ALL_CHECKS_PASSED" for x in decision.findings)


def test_the_headline_reason_is_the_most_consequential_one():
    decision = decide([
        f("TAX_INCLUSIVE_PRESENTATION", subtotal="1", tax="2", rate="18"),
        f("MISSING_INVOICE_NUMBER"),
        f("PO_OVERBILLED", po_number="PO-4510", po_total="1", invoiced="2",
          remaining="3", invoice_total="4", overage="5", band="x"),
    ])
    assert decision.outcome == "NEEDS_REVIEW"
    assert decision.top_reason == "PO_OVERBILLED"


def test_findings_are_ordered_by_severity_then_declaration():
    ordered = sort_findings([
        f("TAX_INCLUSIVE_PRESENTATION", subtotal="1", tax="2", rate="18"),
        f("MISSING_INVOICE_NUMBER"),
        f("VENDOR_BLOCKED", vendor_name="Y", vendor_id="V006", status="blocked"),
    ])
    assert [x.severity for x in ordered] == sorted(
        [x.severity for x in ordered], reverse=True)


def test_blocking_findings_exclude_informational_noise():
    findings = [
        f("TAX_INCLUSIVE_PRESENTATION", subtotal="1", tax="2", rate="18"),
        f("MISSING_INVOICE_NUMBER"),
    ]
    blocking = blocking_findings(findings)
    assert [x.code for x in blocking] == ["MISSING_INVOICE_NUMBER"]


def test_an_approved_invoice_has_no_blocking_findings():
    assert blocking_findings([f("ALL_CHECKS_PASSED")]) == []


def test_the_same_findings_always_produce_the_same_decision():
    """Reproducibility is the entire argument for not letting a model decide."""
    findings = [f("MISSING_INVOICE_NUMBER"),
                f("VENDOR_BANK_MISMATCH", invoice_last4="1", master_last4="2",
                  vendor_name="Y")]
    results = {(decide(list(findings)).outcome, decide(list(findings)).top_reason)
               for _ in range(50)}
    assert len(results) == 1


# ===========================================================================
# The registry itself
# ===========================================================================
def test_every_registered_code_renders_without_its_evidence():
    """A missing evidence key must degrade, never crash a live run."""
    for rc in all_codes():
        assert Finding(rc.code, "test", {}).message
        assert Finding(rc.code, "test", {}).next_action


def test_every_code_has_a_next_action():
    for rc in all_codes():
        assert rc.next_action.strip(), rc.code


def test_unknown_code_fails_loudly():
    with pytest.raises(KeyError):
        get("NO_SUCH_CODE")


def test_severity_ordering_is_what_the_process_map_documents():
    assert Severity.FAIL > Severity.REJECT > Severity.REVIEW > Severity.INFO


# ===========================================================================
# Narrative output
# ===========================================================================
@pytest.fixture
def clean(rules):
    inv, _ = validate_and_normalise(make_extraction(), rules)
    return inv


def test_approved_explanation_names_the_invoice_and_amount(clean, vendor_by_id,
                                                           po_by_number):
    decision = decide([f("VENDOR_MATCHED_BY_GSTIN", gstin="X", vendor_name="Y",
                         vendor_id="V001")])
    text = build_explanation(decision, clean, vendor_by_id["V001"],
                             po_by_number["PO-4501"])
    assert "Approved for payment." in text
    assert "BSI-2026-1042" in text
    assert "1,07,026.00" in text
    assert "PO-4501" in text


def test_held_explanation_states_what_is_wrong_with_numbers(clean, vendor_by_id,
                                                            po_by_number):
    decision = decide([f("PO_OVERBILLED", po_number="PO-4510",
                         po_total="5,01,500.00", invoiced="4,01,200.00",
                         remaining="1,00,300.00", invoice_total="1,50,450.00",
                         overage="50,150.00", band="500.00 cap")])
    text = build_explanation(decision, clean, vendor_by_id["V002"],
                             po_by_number["PO-4510"])
    assert "Held for review." in text
    assert "50,150.00" in text, "the overage must appear in the explanation"
    assert "1,00,300.00" in text


def test_approved_next_action_uses_the_vendor_master_payment_terms(
        clean, vendor_by_id, po_by_number):
    """Invoice dated 2026-11-04, vendor on Net 30 -> 2026-12-04."""
    decision = decide([f("ALL_CHECKS_PASSED")])
    action = build_next_action(decision, clean, vendor_by_id["V001"],
                               po_by_number["PO-4501"])
    assert "2026-12-04" in action


def test_approved_invoices_get_no_draft_email(clean, vendor_by_id, po_by_number):
    decision = decide([f("ALL_CHECKS_PASSED")])
    assert build_draft_email(decision, clean, vendor_by_id["V001"],
                             po_by_number["PO-4501"]) is None


def test_price_variance_drafts_an_email_to_the_vendor(clean, vendor_by_id,
                                                      po_by_number):
    decision = decide([f("PRICE_VARIANCE_EXCEEDS_TOLERANCE", line_count=1,
                         detail="Road freight: PO 8,500.00 vs invoiced 9,180.00 (+8.00%)",
                         threshold_pct="5")])
    email = build_draft_email(decision, clean, vendor_by_id["V003"],
                              po_by_number["PO-4531"])
    assert email is not None
    assert "9,180.00" in email["body"], "the email must quote the evidence"
    assert email["to"].startswith("accounts@")
    assert "credit note" in email["body"]


def test_ambiguous_po_drafts_an_internal_email_not_a_vendor_one(
        clean, vendor_by_id, po_by_number):
    decision = decide([f("PO_INFERRED_AMBIGUOUS", candidate_count=2,
                         detail="PO-4521 (88%), PO-4522 (85%)", margin=0.03,
                         required_margin=0.15)])
    email = build_draft_email(decision, clean, vendor_by_id["V005"], None)
    assert "procurement@" in email["to"]


def test_bank_mismatch_email_warns_against_paying_the_new_account(
        clean, vendor_by_id):
    decision = decide([f("VENDOR_BANK_MISMATCH", invoice_last4="9931",
                         master_last4="4412", vendor_name="Bharat Steel")])
    email = build_draft_email(decision, clean, vendor_by_id["V001"], None)
    assert "procurement@" in email["to"]
    assert "9931" in email["body"] and "4412" in email["body"]


def test_rejected_duplicate_explains_which_invoice_it_duplicates(
        clean, vendor_by_id):
    decision = decide([f("DUPLICATE_CONFIRMED_NUMBER",
                         invoice_number="INV/2026/42",
                         original_number="INV-2026-0042", normalised="INV202642",
                         original_date="2026-10-15", original_amount="94,400.00")])
    text = build_explanation(decision, clean, vendor_by_id["V008"], None)
    assert "Rejected." in text
    assert "INV-2026-0042" in text
    assert "2026-10-15" in text
