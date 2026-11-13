"""Stage 5 - vendor verification.

GSTIN is tried first because it is an identifier; names are a fallback because
they are a description. A vendor whose name on the invoice is a short form of
the legal name on the master ("Bharat Steel Ind.") must still be recognised,
while an unrelated company must not be.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..config import Rules
from ..domain import NormalisedInvoice, Vendor, VendorMatch
from .codes import Finding

STAGE = "vendor"

# Legal-form suffixes carry no identifying information and differ freely
# between a vendor master and what a vendor prints on its own stationery.
_SUFFIXES = [
    "private limited", "pvt limited", "pvt ltd", "pvt. ltd.", "private ltd",
    "limited", "ltd", "llp", "company", "co", "corporation", "corp",
    "incorporated", "inc", "and sons", "& sons",
]


def normalise_name(name: str) -> str:
    text = name.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(" " + suffix):
                text = text[: -len(suffix) - 1].strip()
                changed = True
    return text


def name_similarity(a: str, b: str) -> float:
    """Blend sequence similarity with token overlap.

    Sequence ratio alone punishes a dropped word too harshly ("Bharat Steel
    Ind." vs "Bharat Steel Industries"); token overlap alone ignores order and
    would happily match two companies that share one common word. Taking the
    larger of the two, with token containment as a floor, behaves sensibly on
    both abbreviation and word-order differences.
    """
    na, nb = normalise_name(a), normalise_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(len(ta), len(tb))
    containment = len(ta & tb) / min(len(ta), len(tb))
    # Containment only counts when one name is genuinely a subset of the other
    # (an abbreviation), not when they merely share a generic word.
    subset_bonus = containment if (ta <= tb or tb <= ta) else 0.0
    return round(max(ratio, overlap, subset_bonus), 4)


def match_vendor(
    inv: NormalisedInvoice, vendors: list[Vendor], rules: Rules
) -> tuple[VendorMatch, list[Finding]]:
    findings: list[Finding] = []
    auto = rules.number("vendor.name_match_auto_accept")
    floor = rules.number("vendor.name_match_review_floor")

    # --- 1. GSTIN: an exact identifier beats any name similarity ----------
    if inv.vendor_gstin:
        for vendor in vendors:
            if vendor.gstin.upper() == inv.vendor_gstin.upper():
                findings.append(Finding("VENDOR_MATCHED_BY_GSTIN", STAGE, {
                    "gstin": vendor.gstin, "vendor_name": vendor.legal_name,
                    "vendor_id": vendor.vendor_id,
                }))
                match = VendorMatch(vendor=vendor, method="gstin", score=1.0)
                findings.extend(_status_and_bank_checks(inv, vendor, rules))
                return match, findings

    # --- 2. Name / alias similarity ---------------------------------------
    scored: list[tuple[float, Vendor, str]] = []
    if inv.vendor_name:
        for vendor in vendors:
            best, best_name = 0.0, vendor.legal_name
            for candidate in vendor.all_names:
                score = name_similarity(inv.vendor_name, candidate)
                if score > best:
                    best, best_name = score, candidate
            scored.append((best, vendor, best_name))
    scored.sort(key=lambda row: row[0], reverse=True)
    candidates = [
        {"vendor_id": v.vendor_id, "legal_name": v.legal_name,
         "matched_on": matched, "score": round(score, 4), "status": v.status}
        for score, v, matched in scored[:5]
    ]

    if not scored or scored[0][0] < floor:
        best_score = scored[0][0] if scored else 0.0
        best_name = scored[0][1].legal_name if scored else "-"
        findings.append(Finding("VENDOR_NOT_ON_MASTER", STAGE, {
            "invoice_vendor": inv.vendor_name or "(not read)",
            "best_name": best_name, "best_score": best_score, "floor": floor,
        }))
        return VendorMatch(None, "none", best_score, candidates), findings

    score, vendor, matched_on = scored[0]
    if score < auto:
        findings.append(Finding("VENDOR_FUZZY_MATCH_LOW", STAGE, {
            "invoice_vendor": inv.vendor_name, "master_name": vendor.legal_name,
            "score": score, "threshold": auto, "vendor_id": vendor.vendor_id,
        }))
    else:
        findings.append(Finding("VENDOR_MATCHED_BY_NAME", STAGE, {
            "invoice_vendor": inv.vendor_name, "master_name": matched_on,
            "vendor_id": vendor.vendor_id, "score": score,
        }))

    findings.extend(_status_and_bank_checks(inv, vendor, rules))
    return VendorMatch(vendor, "name", score, candidates), findings


def _status_and_bank_checks(
    inv: NormalisedInvoice, vendor: Vendor, rules: Rules
) -> list[Finding]:
    findings: list[Finding] = []

    if vendor.status == "blocked":
        findings.append(Finding("VENDOR_BLOCKED", STAGE, {
            "vendor_name": vendor.legal_name, "vendor_id": vendor.vendor_id,
            "status": vendor.status,
        }))
    elif vendor.status == "pending":
        findings.append(Finding("VENDOR_PENDING", STAGE, {
            "vendor_name": vendor.legal_name, "vendor_id": vendor.vendor_id,
            "status": vendor.status,
        }))

    # GSTIN present on the invoice but belonging to someone else: either the
    # invoice is wrong or the master is stale. Either way a human decides.
    if inv.vendor_gstin and vendor.gstin.upper() != inv.vendor_gstin.upper():
        findings.append(Finding("GSTIN_MISMATCH", STAGE, {
            "invoice_gstin": inv.vendor_gstin, "master_gstin": vendor.gstin,
            "vendor_name": vendor.legal_name,
        }))

    if (rules.boolean("vendor.bank_mismatch_requires_review")
            and inv.bank_account_last4
            and inv.bank_account_last4 != vendor.bank_account_last4):
        findings.append(Finding("VENDOR_BANK_MISMATCH", STAGE, {
            "invoice_last4": inv.bank_account_last4,
            "master_last4": vendor.bank_account_last4,
            "vendor_name": vendor.legal_name,
        }))
    return findings
