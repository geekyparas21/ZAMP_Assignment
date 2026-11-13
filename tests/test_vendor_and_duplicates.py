"""Vendor verification and duplicate detection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.rules.duplicates import detect_duplicates
from backend.app.rules.validation import validate_and_normalise
from backend.app.rules.vendor import match_vendor, name_similarity, normalise_name

from .conftest import make_extraction

D = Decimal


def codes(findings):
    return {f.code for f in findings}


def normalised(rules, **overrides):
    inv, _ = validate_and_normalise(make_extraction(**overrides), rules)
    return inv


# --- name normalisation and similarity ------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Bharat Steel Industries Private Limited", "bharat steel industries"),
    ("Nova Polymers Pvt Ltd", "nova polymers"),
    ("Trident Fasteners & Hardware", "trident fasteners and hardware"),
    ("Orient Electricals Ltd", "orient electricals"),
])
def test_legal_suffixes_are_stripped(raw, expected):
    assert normalise_name(raw) == expected


def test_abbreviated_name_matches_the_legal_name():
    score = name_similarity("Bharat Steel Ind.", "Bharat Steel Industries Private Limited")
    assert score >= 0.70


def test_unrelated_companies_do_not_match():
    score = name_similarity("Bharat Steel Ind.", "Nova Polymers Private Limited")
    assert score < 0.70


def test_sharing_one_generic_word_is_not_a_match():
    """'Precision' alone must not link two different suppliers."""
    score = name_similarity("Deccan Precision Tools", "Mumbai Precision Castings")
    assert score < 0.70


# --- vendor matching -------------------------------------------------------
def test_gstin_identifies_the_vendor_exactly(rules, vendors):
    match, findings = match_vendor(normalised(rules), vendors, rules)
    assert match.vendor.vendor_id == "V001"
    assert match.method == "gstin"
    assert match.score == 1.0
    assert "VENDOR_MATCHED_BY_GSTIN" in codes(findings)


def test_vendor_is_found_by_name_when_no_gstin_is_present(rules, vendors):
    match, findings = match_vendor(normalised(rules, vendor_gstin=None), vendors, rules)
    assert match.vendor.vendor_id == "V001"
    assert match.method == "name"
    assert match.score >= 0.70


def test_unknown_vendor_is_held_for_onboarding_not_rejected(rules, vendors):
    """An unknown supplier may simply be a new one.

    Paying a vendor who is not on the master is still the expensive mistake,
    and nothing here approves one - but refusing the invoice outright is the
    wrong hold. In an AP team this routes to procurement for onboarding, so
    the finding is REVIEW and the vendor stays unmatched. Only a *blocked*
    vendor is a refusal (see test_blocked_vendor_is_flagged).
    """
    from backend.app.rules.codes import Severity, get

    match, findings = match_vendor(
        normalised(rules, vendor_name="Shadow Trading Enterprises", vendor_gstin=None),
        vendors, rules)
    assert match.vendor is None
    assert "VENDOR_UNKNOWN" in codes(findings)
    assert get("VENDOR_UNKNOWN").severity == Severity.REVIEW


def test_an_unknown_vendor_finding_carries_what_onboarding_needs(rules, vendors):
    """The evidence is what the draft email is written from, not free text."""
    _, findings = match_vendor(
        normalised(rules, vendor_name="Shadow Trading Enterprises",
                   vendor_gstin="27AAMCM1234N1Z5"),
        vendors, rules)
    evidence = next(f for f in findings if f.code == "VENDOR_UNKNOWN").evidence

    assert evidence["invoice_vendor"] == "Shadow Trading Enterprises"
    assert evidence["invoice_gstin"] == "27AAMCM1234N1Z5"
    assert evidence["invoice_number"] == "BSI-2026-1042"
    assert evidence["invoice_date"] == "2026-11-04"
    assert evidence["invoice_total"] == "1,07,026.00"
    assert evidence["best_score"] < evidence["floor"]


def test_blocked_vendor_is_flagged(rules, vendors):
    _, findings = match_vendor(
        normalised(rules, vendor_name="Orient Electricals Ltd",
                   vendor_gstin="07AAGCO2345J1ZD", bank_account_last4="1187"),
        vendors, rules)
    assert "VENDOR_BLOCKED" in codes(findings)


def test_pending_vendor_is_held_not_rejected(rules, vendors):
    _, findings = match_vendor(
        normalised(rules, vendor_name="Himalaya Chemicals Pvt Ltd",
                   vendor_gstin="06AAHCH6789K1ZV", bank_account_last4="3344"),
        vendors, rules)
    assert "VENDOR_PENDING" in codes(findings)


def test_changed_bank_account_is_flagged_as_fraud_risk(rules, vendors):
    _, findings = match_vendor(normalised(rules, bank_account_last4="9931"),
                               vendors, rules)
    assert "VENDOR_BANK_MISMATCH" in codes(findings)


def test_matching_bank_account_raises_nothing(rules, vendors):
    _, findings = match_vendor(normalised(rules, bank_account_last4="4412"),
                               vendors, rules)
    assert "VENDOR_BANK_MISMATCH" not in codes(findings)


def test_gstin_belonging_to_another_vendor_is_flagged(rules, vendors):
    """Name says Bharat Steel, GSTIN says someone else."""
    _, findings = match_vendor(
        normalised(rules, vendor_gstin="29AACCN5678E1Z5"), vendors, rules)
    # GSTIN wins the identification, so the name is the odd one out - either
    # way a human sees a mismatch rather than a silent pick.
    assert codes(findings) & {"GSTIN_MISMATCH", "VENDOR_MATCHED_BY_GSTIN"}


# --- duplicate detection ---------------------------------------------------
def test_identical_file_hash_is_a_confirmed_duplicate(rules, vendors, ledger):
    vendor = next(v for v in vendors if v.vendor_id == "V008")
    entries = [e for e in ledger if e.vendor_id == "V008"]
    entries[0].file_sha256 = "a" * 64
    findings = detect_duplicates(normalised(rules), vendor, entries, "a" * 64, rules)
    assert "DUPLICATE_CONFIRMED_HASH" in codes(findings)
    entries[0].file_sha256 = None


def test_reformatted_invoice_number_is_caught_as_a_duplicate(rules, vendors, ledger):
    """Scenario 3: INV/2026/42 is the paid INV-2026-0042 wearing a disguise."""
    vendor = next(v for v in vendors if v.vendor_id == "V008")
    inv = normalised(rules, vendor_name="Vega Instruments Pvt Ltd",
                     vendor_gstin="19AAICV0123L1Z7", invoice_number="INV/2026/42",
                     invoice_date="2026-10-15", total=D("94400.00"),
                     subtotal=D("80000.00"), tax_total=D("14400.00"))
    findings = detect_duplicates(inv, vendor, ledger, "different-hash", rules)
    assert "DUPLICATE_CONFIRMED_NUMBER" in codes(findings)
    evidence = next(f for f in findings if f.code == "DUPLICATE_CONFIRMED_NUMBER").evidence
    assert evidence["original_number"] == "INV-2026-0042"


def test_same_amount_different_number_within_the_window_is_only_suspected(
        rules, vendors, ledger):
    """Scenario 3b: held for a human, never auto-rejected."""
    vendor = next(v for v in vendors if v.vendor_id == "V010")
    inv = normalised(rules, vendor_name="Suryodaya Engg Works",
                     vendor_gstin="08AAKCS8901N1ZP", invoice_number="SEW-2026-131",
                     invoice_date="2026-11-12", total=D("72570.00"),
                     subtotal=D("61500.00"), tax_total=D("11070.00"))
    findings = detect_duplicates(inv, vendor, ledger, "other-hash", rules)
    assert "DUPLICATE_SUSPECTED" in codes(findings)
    assert "DUPLICATE_CONFIRMED_NUMBER" not in codes(findings)
    assert next(f for f in findings).evidence["days"] == 2


def test_same_amount_outside_the_window_is_not_suspicious(rules, vendors, ledger):
    vendor = next(v for v in vendors if v.vendor_id == "V010")
    inv = normalised(rules, vendor_name="Suryodaya Engg Works",
                     vendor_gstin="08AAKCS8901N1ZP", invoice_number="SEW-2026-190",
                     invoice_date="2026-12-20", total=D("72570.00"),
                     subtotal=D("61500.00"), tax_total=D("11070.00"))
    findings = detect_duplicates(inv, vendor, ledger, "other-hash", rules)
    assert codes(findings) == set()


def test_a_fresh_invoice_from_a_known_vendor_is_not_a_duplicate(rules, vendors, ledger):
    vendor = next(v for v in vendors if v.vendor_id == "V001")
    findings = detect_duplicates(normalised(rules), vendor, ledger, "new-hash", rules)
    assert codes(findings) == set()


def test_previously_rejected_invoice_does_not_block_its_resubmission(
        rules, vendors, ledger):
    """A corrected resubmission must be processable, so only committed
    outcomes (approved/held) count as a prior."""
    vendor = next(v for v in vendors if v.vendor_id == "V008")
    entries = [e for e in ledger if e.vendor_id == "V008"]
    for e in entries:
        e.outcome = "REJECTED"
    inv = normalised(rules, vendor_name="Vega Instruments Pvt Ltd",
                     vendor_gstin="19AAICV0123L1Z7", invoice_number="INV/2026/42",
                     invoice_date="2026-10-15", total=D("94400.00"),
                     subtotal=D("80000.00"), tax_total=D("14400.00"))
    findings = detect_duplicates(inv, vendor, entries, "x", rules)
    assert codes(findings) == set()
    for e in entries:
        e.outcome = "APPROVED"
