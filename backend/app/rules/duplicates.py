"""Stage 6 - duplicate detection.

Three checks, deliberately separated by how certain they are:

  a. Same file bytes          -> certain      -> REJECT
  b. Same vendor + number     -> certain      -> REJECT
  c. Same vendor + amount,
     within a few days        -> suspicious   -> REVIEW

The distinction matters. (a) and (b) identify *the same invoice*. (c) only
says two invoices look alike, which a vendor making two genuine deliveries of
the same goods in one week would also produce - rejecting that outright would
block legitimate payments, so it goes to a human instead.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import Rules
from ..money import format_inr
from ..domain import LedgerEntry, NormalisedInvoice, Vendor
from .codes import Finding
from .validation import normalise_invoice_number

STAGE = "duplicates"
CENT = Decimal("0.01")

#: Outcomes that mean money is committed. A previously rejected invoice is not
#: a reason to reject its corrected resubmission.
_BLOCKING_OUTCOMES = {"APPROVED", "NEEDS_REVIEW"}


def detect_duplicates(
    inv: NormalisedInvoice,
    vendor: Vendor | None,
    ledger: list[LedgerEntry],
    file_sha256: str | None,
    rules: Rules,
) -> list[Finding]:
    findings: list[Finding] = []
    relevant = [e for e in ledger if e.outcome in _BLOCKING_OUTCOMES]

    # --- a. identical file -------------------------------------------------
    if file_sha256:
        for entry in relevant:
            if entry.file_sha256 and entry.file_sha256 == file_sha256:
                findings.append(Finding("DUPLICATE_CONFIRMED_HASH", STAGE, {
                    "original_number": entry.invoice_number,
                    "original_date": _d(entry.invoice_date),
                    "original_amount": format_inr(entry.total_amount),
                    "sha256": file_sha256[:16] + "...",
                }))
                return findings     # nothing further can add to certainty

    if vendor is None:
        return findings             # without a vendor, (b) and (c) are meaningless

    same_vendor = [e for e in relevant if e.vendor_id == vendor.vendor_id]

    # --- b. same vendor, same normalised invoice number --------------------
    if inv.invoice_number_normalised:
        for entry in same_vendor:
            if normalise_invoice_number(entry.invoice_number) == inv.invoice_number_normalised:
                findings.append(Finding("DUPLICATE_CONFIRMED_NUMBER", STAGE, {
                    "invoice_number": inv.invoice_number,
                    "original_number": entry.invoice_number,
                    "normalised": inv.invoice_number_normalised,
                    "original_date": _d(entry.invoice_date),
                    "original_amount": format_inr(entry.total_amount),
                    "original_invoice_id": entry.invoice_id,
                }))
                return findings

    # --- c. same vendor, same amount, close in time ------------------------
    if inv.total is None or inv.invoice_date is None:
        return findings
    window = rules.integer("duplicates.suspected_window_days")
    amount_tol = rules.decimal("duplicates.suspected_amount_tolerance_abs")

    for entry in same_vendor:
        if entry.invoice_date is None:
            continue
        if abs(entry.total_amount - inv.total) > amount_tol:
            continue
        days = abs((inv.invoice_date - entry.invoice_date).days)
        if days <= window:
            findings.append(Finding("DUPLICATE_SUSPECTED", STAGE, {
                "invoice_number": inv.invoice_number or "(none)",
                "invoice_date": _d(inv.invoice_date),
                "original_number": entry.invoice_number,
                "original_date": _d(entry.invoice_date),
                "amount": format_inr(inv.total), "days": days, "window": window,
                "original_invoice_id": entry.invoice_id,
            }))
            break
    return findings




def _d(value) -> str:
    return "-" if value is None else value.isoformat()
