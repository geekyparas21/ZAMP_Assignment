"""The reason-code registry.

Every conclusion the system reaches is expressed as a ReasonCode. A code owns
its severity, the sentence an AP clerk reads, and the shape of the evidence it
must carry. Nothing in the pipeline is allowed to assert an outcome without
emitting one of these, which is what makes "why was this held?" answerable
from the screen rather than from the source.

Declaration order inside each severity is deliberate: it is the tie-break that
decides which reason is shown as the headline on the dashboard, so the most
consequential cause is listed first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    """Ordered so that max() over a set of findings yields the outcome.

    FAIL sits above REJECT because a document the system could not read at all
    is not a policy judgement - it is an unanswered question, and must never
    masquerade as a decision.
    """

    INFO = 0
    REVIEW = 1
    REJECT = 2
    FAIL = 3


OUTCOME_FOR_SEVERITY = {
    Severity.INFO: "APPROVED",
    Severity.REVIEW: "NEEDS_REVIEW",
    Severity.REJECT: "REJECTED",
    Severity.FAIL: "FAILED",
}


@dataclass(frozen=True)
class ReasonCode:
    code: str
    severity: Severity
    title: str
    #: Format string rendered against a finding's evidence dict.
    template: str
    #: What an AP clerk should do about it.
    next_action: str
    order: int = 0


_REGISTRY: dict[str, ReasonCode] = {}


def _register(code: str, severity: Severity, title: str, template: str,
              next_action: str) -> ReasonCode:
    rc = ReasonCode(code, severity, title, template, next_action, order=len(_REGISTRY))
    _REGISTRY[code] = rc
    return rc


def get(code: str) -> ReasonCode:
    try:
        return _REGISTRY[code]
    except KeyError:  # pragma: no cover - guards against typos at call sites
        raise KeyError(f"unknown reason code {code!r}") from None


def all_codes() -> list[ReasonCode]:
    return sorted(_REGISTRY.values(), key=lambda rc: (-rc.severity, rc.order))


# ===========================================================================
# FAILED - the system could not process the input at all.
# ===========================================================================
FILE_NOT_PDF = _register(
    "FILE_NOT_PDF", Severity.FAIL, "Not a PDF",
    "The uploaded file is not a PDF (detected: {detected}).",
    "Ask the sender to resend the invoice as a PDF.")
FILE_EMPTY = _register(
    "FILE_EMPTY", Severity.FAIL, "Empty file",
    "The uploaded file is empty (0 bytes).",
    "Ask the sender to resend the invoice.")
FILE_CORRUPT = _register(
    "FILE_CORRUPT", Severity.FAIL, "Corrupt PDF",
    "The PDF could not be opened: {detail}",
    "Ask the sender to resend the invoice.")
FILE_ENCRYPTED = _register(
    "FILE_ENCRYPTED", Severity.FAIL, "Password-protected PDF",
    "The PDF is password-protected and cannot be read.",
    "Ask the sender for an unprotected copy.")
LLM_RATE_LIMITED = _register(
    "LLM_RATE_LIMITED", Severity.FAIL, "AI provider rate limit reached",
    "The extraction provider returned a rate-limit error after {attempts} attempts.",
    "Wait a minute and retry the run.")
LLM_UNAVAILABLE = _register(
    "LLM_UNAVAILABLE", Severity.FAIL, "AI provider unavailable",
    "The extraction provider failed after {attempts} attempts: {detail}",
    "Retry the run; if it persists, check the provider status.")
EXTRACTION_SCHEMA_INVALID = _register(
    "EXTRACTION_SCHEMA_INVALID", Severity.FAIL, "Unreadable extraction result",
    "The model's output failed schema validation after {attempts} attempts: {detail}",
    "Retry the run. If it repeats, the document may be unusually formatted.")

# ===========================================================================
# REJECTED - a clear policy violation.
# ===========================================================================
DUPLICATE_CONFIRMED_HASH = _register(
    "DUPLICATE_CONFIRMED_HASH", Severity.REJECT, "Confirmed duplicate (identical file)",
    "This is byte-for-byte the same file as invoice {original_number}, "
    "processed on {original_date}.",
    "No action needed - the original was already processed. Tell the vendor it is a duplicate.")
DUPLICATE_CONFIRMED_NUMBER = _register(
    "DUPLICATE_CONFIRMED_NUMBER", Severity.REJECT, "Confirmed duplicate (same invoice number)",
    "Invoice {invoice_number} matches already-processed invoice {original_number} "
    "from the same vendor (both normalise to {normalised}), dated {original_date} "
    "for {original_amount}.",
    "Reject and notify the vendor that this invoice was already settled.")
VENDOR_BLOCKED = _register(
    "VENDOR_BLOCKED", Severity.REJECT, "Vendor is blocked",
    "{vendor_name} ({vendor_id}) is marked '{status}' on the vendor master.",
    "Do not pay. Route to procurement to confirm the block before any further action.")
VENDOR_NOT_ON_MASTER = _register(
    "VENDOR_NOT_ON_MASTER", Severity.REJECT, "Vendor not on the approved list",
    "No approved vendor matches '{invoice_vendor}'. Closest was {best_name} "
    "at {best_score:.0%}, below the {floor:.0%} threshold.",
    "Do not pay. Send to vendor onboarding if this supplier is legitimate.")
PO_CLOSED = _register(
    "PO_CLOSED", Severity.REJECT, "Purchase order is closed",
    "{po_number} is closed, with {invoiced} of {po_total} already invoiced.",
    "Reject, or ask procurement to reopen or raise a new PO.")
NOT_AN_INVOICE = _register(
    "NOT_AN_INVOICE", Severity.REJECT, "Not an invoice",
    "This document is a {document_type}, not an invoice.",
    "No payment is due. File the document and take no further action.")

# ===========================================================================
# NEEDS_REVIEW - a human must look. Ordered most consequential first.
# ===========================================================================
PO_OVERBILLED = _register(
    "PO_OVERBILLED", Severity.REVIEW, "Exceeds the remaining PO balance",
    "{po_number} has {remaining} remaining of {po_total} ({invoiced} already invoiced), "
    "but this invoice is for {invoice_total} - over by {overage}.",
    "Route to procurement: either raise a PO amendment for {overage} or ask the vendor "
    "to credit the excess.")
PRICE_VARIANCE_EXCEEDS_TOLERANCE = _register(
    "PRICE_VARIANCE_EXCEEDS_TOLERANCE", Severity.REVIEW, "Unit price above the PO rate",
    "{line_count} line(s) are priced above the PO rate by more than {threshold_pct}%: "
    "{detail}.",
    "Ask the vendor to justify the increase or issue a credit note for the difference.")
QTY_VARIANCE_EXCEEDS_TOLERANCE = _register(
    "QTY_VARIANCE_EXCEEDS_TOLERANCE", Severity.REVIEW, "Quantity above the PO quantity",
    "{line_count} line(s) bill more than the PO quantity by over {threshold_pct}%: {detail}.",
    "Confirm with stores that the extra quantity was received before paying.")
TOTAL_VARIANCE_EXCEEDS_TOLERANCE = _register(
    "TOTAL_VARIANCE_EXCEEDS_TOLERANCE", Severity.REVIEW, "Total outside tolerance",
    "Invoice total {actual} exceeds the expected {expected} by {difference} "
    "({pct:.2f}%), outside the permitted {band}.",
    "Check the variance with the vendor before paying.")
VENDOR_BANK_MISMATCH = _register(
    "VENDOR_BANK_MISMATCH", Severity.REVIEW, "Bank details differ from the vendor master",
    "The invoice quotes account ending {invoice_last4}, but the vendor master holds "
    "{master_last4}.",
    "Do NOT pay to the new account. Verify the change by phoning a known contact on a "
    "number you already hold - this is the most common payment-redirection fraud.")
DUPLICATE_SUSPECTED = _register(
    "DUPLICATE_SUSPECTED", Severity.REVIEW, "Possible duplicate",
    "Invoice {invoice_number} ({invoice_date}) has the same vendor and amount "
    "{amount} as {original_number} ({original_date}), {days} day(s) apart.",
    "Confirm with the vendor whether this is a second genuine delivery or a re-send.")
PO_INFERRED_AMBIGUOUS = _register(
    "PO_INFERRED_AMBIGUOUS", Severity.REVIEW, "More than one PO could match",
    "No PO was quoted. {candidate_count} open POs match closely: {detail}. "
    "The top two are within {margin:.0%} of each other.",
    "Pick the correct PO. Ask the vendor to quote the PO number on future invoices.")
PO_INFERRED_LOW_CONFIDENCE = _register(
    "PO_INFERRED_LOW_CONFIDENCE", Severity.REVIEW, "PO match is not confident",
    "No PO was quoted. The best candidate {po_number} scored only {score:.0%}, "
    "below the {threshold:.0%} required to proceed automatically.",
    "Confirm the correct PO before paying.")
PO_NOT_FOUND = _register(
    "PO_NOT_FOUND", Severity.REVIEW, "PO reference not found",
    "The invoice quotes '{po_reference}' but no such PO exists, and no open PO for "
    "this vendor matches the amount.",
    "Check the PO number with the vendor or procurement.")
PO_VENDOR_MISMATCH = _register(
    "PO_VENDOR_MISMATCH", Severity.REVIEW, "PO belongs to a different vendor",
    "{po_number} was raised for {po_vendor}, but this invoice is from {invoice_vendor}.",
    "Confirm the correct PO with procurement.")
LINE_NOT_ON_PO = _register(
    "LINE_NOT_ON_PO", Severity.REVIEW, "Line item is not on the PO",
    "{line_count} invoiced line(s) have no match on {po_number}: {detail}.",
    "Confirm with procurement that the extra items were ordered.")
CURRENCY_MISMATCH = _register(
    "CURRENCY_MISMATCH", Severity.REVIEW, "Currency differs from the PO",
    "The invoice is in {invoice_currency} but {po_number} is in {po_currency}.",
    "Confirm the correct currency; this system does not convert between currencies.")
VENDOR_PENDING = _register(
    "VENDOR_PENDING", Severity.REVIEW, "Vendor onboarding is incomplete",
    "{vendor_name} ({vendor_id}) is still '{status}' on the vendor master.",
    "Complete vendor onboarding before releasing payment.")
VENDOR_FUZZY_MATCH_LOW = _register(
    "VENDOR_FUZZY_MATCH_LOW", Severity.REVIEW, "Vendor matched only loosely",
    "'{invoice_vendor}' was matched to {master_name} at {score:.0%}, below the "
    "{threshold:.0%} needed to accept automatically.",
    "Confirm this is the right vendor, and add the name variant to the master.")
GSTIN_MISMATCH = _register(
    "GSTIN_MISMATCH", Severity.REVIEW, "GSTIN differs from the vendor master",
    "The invoice shows GSTIN {invoice_gstin}, but the master holds {master_gstin} "
    "for {vendor_name}.",
    "Verify which GSTIN is correct before paying; a wrong GSTIN blocks input tax credit.")
GSTIN_INVALID_FORMAT = _register(
    "GSTIN_INVALID_FORMAT", Severity.REVIEW, "GSTIN is not valid",
    "'{gstin}' is not a valid GSTIN ({problem}).",
    "Ask the vendor for a corrected invoice; an invalid GSTIN blocks input tax credit.")
MISSING_INVOICE_NUMBER = _register(
    "MISSING_INVOICE_NUMBER", Severity.REVIEW, "No invoice number",
    "The document carries no invoice number.",
    "Ask the vendor to reissue with an invoice number - it cannot be tracked or "
    "de-duplicated without one.")
MISSING_INVOICE_DATE = _register(
    "MISSING_INVOICE_DATE", Severity.REVIEW, "No invoice date",
    "The document carries no invoice date.",
    "Ask the vendor to reissue with a date; payment terms cannot be computed without it.")
MISSING_TOTAL = _register(
    "MISSING_TOTAL", Severity.REVIEW, "No total amount",
    "No payable total could be read from the document.",
    "Ask the vendor to reissue with a clear total.")
LOW_CONFIDENCE_CRITICAL_FIELD = _register(
    "LOW_CONFIDENCE_CRITICAL_FIELD", Severity.REVIEW, "A key field could not be read reliably",
    "Extraction confidence for {detail} fell below {threshold:.0%}.",
    "Check the highlighted field(s) against the document image before approving.")
ARITHMETIC_LINES_VS_SUBTOTAL = _register(
    "ARITHMETIC_LINES_VS_SUBTOTAL", Severity.REVIEW, "Line items do not sum to the subtotal",
    "The line items add up to {line_sum}, but the stated subtotal is {subtotal} "
    "(out by {difference}).",
    "Check the document; the invoice does not add up.")
ARITHMETIC_SUBTOTAL_TAX_TOTAL = _register(
    "ARITHMETIC_SUBTOTAL_TAX_TOTAL", Severity.REVIEW, "Subtotal plus tax does not equal the total",
    "{subtotal} + {tax} = {computed}, but the stated total is {total} (out by {difference}).",
    "Check the document; the invoice does not add up.")

# ===========================================================================
# INFO - recorded and shown, never blocks payment.
# ===========================================================================
TOTAL_VARIANCE_WITHIN_TOLERANCE = _register(
    "TOTAL_VARIANCE_WITHIN_TOLERANCE", Severity.INFO, "Small variance, within tolerance",
    "Invoice total {actual} differs from the expected {expected} by {difference} "
    "({pct:.2f}%), inside the permitted {band}.",
    "No action needed - the variance is within policy.")
PO_INFERRED_HIGH_CONFIDENCE = _register(
    "PO_INFERRED_HIGH_CONFIDENCE", Severity.INFO, "PO was inferred, not quoted",
    "The invoice quotes no PO. {po_number} was inferred at {score:.0%} confidence "
    "({reason}), well clear of the next candidate.",
    "Ask the vendor to quote the PO number on future invoices.")
PARTIAL_INVOICE_AGAINST_PO = _register(
    "PARTIAL_INVOICE_AGAINST_PO", Severity.INFO, "Part-billing against the PO",
    "This invoice bills {invoice_total} of {po_number}; {remaining_after} will "
    "remain of {po_total}.",
    "No action needed - partial billing is expected on this PO.")
TAX_INCLUSIVE_PRESENTATION = _register(
    "TAX_INCLUSIVE_PRESENTATION", Severity.INFO, "Rates quoted inclusive of GST",
    "Rates are GST-inclusive. Taxable value {subtotal} and GST {tax} were "
    "back-computed at {rate}% for comparison with the PO.",
    "No action needed.")
SCANNED_DOCUMENT = _register(
    "SCANNED_DOCUMENT", Severity.INFO, "Scanned document",
    "No text layer was found ({chars} characters over {pages} page(s)), so the pages "
    "were read as images.",
    "No action needed.")
ALL_CHECKS_PASSED = _register(
    "ALL_CHECKS_PASSED", Severity.INFO, "All checks passed",
    "Vendor, PO, amounts and duplicate checks all passed with no variance.",
    "Approve for payment.")

# Declared after ALL_CHECKS_PASSED on purpose. These record *how* something was
# identified, which is useful inside an expanded stage but is never the most
# interesting thing to show as a run's headline reason on the dashboard.
VENDOR_MATCHED_BY_GSTIN = _register(
    "VENDOR_MATCHED_BY_GSTIN", Severity.INFO, "Vendor identified by GSTIN",
    "GSTIN {gstin} matched {vendor_name} ({vendor_id}) exactly.",
    "No action needed.")
VENDOR_MATCHED_BY_NAME = _register(
    "VENDOR_MATCHED_BY_NAME", Severity.INFO, "Vendor identified by name",
    "'{invoice_vendor}' matched {master_name} ({vendor_id}) at {score:.0%}.",
    "No action needed.")
PO_MATCHED_EXPLICIT = _register(
    "PO_MATCHED_EXPLICIT", Severity.INFO, "PO matched from the invoice",
    "The invoice quotes {po_reference}, matched to {po_number}.",
    "No action needed.")


@dataclass
class Finding:
    """One rule firing, with the evidence that made it fire."""

    code: str
    stage: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def reason(self) -> ReasonCode:
        return get(self.code)

    @property
    def severity(self) -> Severity:
        return self.reason.severity

    @property
    def message(self) -> str:
        """Render the human sentence, filling the template from the evidence.

        A missing evidence key is a programming error, but it must never take
        the pipeline down mid-run: the raw template is surfaced instead so the
        gap is visible rather than silent.
        """
        try:
            return self.reason.template.format(**self.evidence)
        except (KeyError, IndexError, ValueError):
            return self.reason.template

    @property
    def next_action(self) -> str:
        try:
            return self.reason.next_action.format(**self.evidence)
        except (KeyError, IndexError, ValueError):
            return self.reason.next_action

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.name,
            "title": self.reason.title,
            "message": self.message,
            "next_action": self.next_action,
            "stage": self.stage,
            "evidence": self.evidence,
        }


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Most consequential first: severity, then registry declaration order."""
    return sorted(findings, key=lambda f: (-f.severity, f.reason.order))
