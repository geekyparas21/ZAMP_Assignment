"""Stages 4-10 as one deterministic function.

Everything from validation to the drafted email happens here, with no network
access and no model call. The pipeline orchestrator adds stages 1-3 (ingest,
document analysis, extraction) around it; the tests call it directly, so what
the tests prove is what production runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..config import Rules, get_rules
from ..domain import (Decision, ExtractedInvoice, LedgerEntry, NormalisedInvoice,
                      POMatch, PurchaseOrder, Reconciliation, Vendor, VendorMatch)
from ..money import format_inr
from .codes import Finding
from .duplicates import detect_duplicates
from .engine import decide
from .narrative import build_draft_email, build_explanation, build_next_action
from .po_matching import match_po
from .reconciliation import reconcile
from .validation import validate_and_normalise
from .vendor import match_vendor


@dataclass
class ReferenceData:
    vendors: list[Vendor]
    purchase_orders: list[PurchaseOrder]
    ledger: list[LedgerEntry]


@dataclass
class EvaluationResult:
    decision: Decision
    invoice: NormalisedInvoice | None
    vendor_match: VendorMatch | None
    po_match: POMatch | None
    reconciliation: Reconciliation | None
    findings: list[Finding] = field(default_factory=list)
    #: Per-stage findings, so the UI can show which stage produced what.
    stage_findings: dict[str, list[Finding]] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        return self.decision.outcome

    @property
    def codes(self) -> set[str]:
        return {f.code for f in self.findings}

    @property
    def matched_po(self) -> PurchaseOrder | None:
        return self.po_match.po if self.po_match else None

    @property
    def vendor(self) -> Vendor | None:
        return self.vendor_match.vendor if self.vendor_match else None


def evaluate(
    extracted: ExtractedInvoice,
    reference: ReferenceData,
    file_sha256: str | None = None,
    rules: Rules | None = None,
    prior_findings: list[Finding] | None = None,
) -> EvaluationResult:
    """Run every deterministic check and produce one decision.

    `prior_findings` carries anything the earlier stages already observed -
    for example that the document was a scan - so it lands in the same
    decision and the same audit trail.
    """
    rules = rules or get_rules()
    findings: list[Finding] = list(prior_findings or [])
    stages: dict[str, list[Finding]] = {}

    def record(stage: str, produced: list[Finding]) -> None:
        stages[stage] = produced
        findings.extend(produced)

    # --- Not an invoice: stop before pretending to reconcile one -----------
    if not extracted.is_invoice:
        produced = [Finding("NOT_AN_INVOICE", "extraction",
                            {"document_type": extracted.document_type or "document"})]
        record("extraction", produced)
        decision = decide(findings)
        decision.explanation = build_explanation(decision, None, None, None)
        decision.next_action = build_next_action(decision, None, None, None)
        decision.draft_email = build_draft_email(decision, None, None, None)
        return EvaluationResult(decision, None, None, None, None, findings, stages)

    # --- 4. Validation and normalisation -----------------------------------
    invoice, produced = validate_and_normalise(extracted, rules)
    record("validation", produced)

    # --- 5. Vendor verification --------------------------------------------
    vendor_match, produced = match_vendor(invoice, reference.vendors, rules)
    record("vendor", produced)
    vendor = vendor_match.vendor

    # --- 6. Duplicate detection --------------------------------------------
    produced = detect_duplicates(invoice, vendor, reference.ledger, file_sha256, rules)
    record("duplicates", produced)

    # --- 7. PO matching -----------------------------------------------------
    po_match, produced = match_po(invoice, vendor, reference.purchase_orders, rules)
    record("po_matching", produced)

    # --- 8. Reconciliation ---------------------------------------------------
    recon, produced = reconcile(invoice, po_match.po, rules)
    record("reconciliation", produced)

    # --- 9. Decision ---------------------------------------------------------
    decision = decide(findings)

    # --- 10. Output ----------------------------------------------------------
    decision.explanation = build_explanation(decision, invoice, vendor, po_match.po)
    decision.next_action = build_next_action(decision, invoice, vendor, po_match.po)
    decision.draft_email = build_draft_email(decision, invoice, vendor, po_match.po)

    return EvaluationResult(decision, invoice, vendor_match, po_match, recon,
                            decision.findings, stages)


def apply_approval(result: EvaluationResult) -> dict[str, Any] | None:
    """The side effect of an approval: move the PO's invoiced-to-date.

    Returned as a description rather than applied in place, so the caller
    owns the database write and the UI can show exactly what changed. This is
    what makes the split-PO scenario work across consecutive runs.
    """
    if result.outcome != "APPROVED":
        return None
    po = result.matched_po
    invoice = result.invoice
    if po is None or invoice is None or invoice.total is None:
        return None

    before = po.amount_invoiced_to_date
    after = (before + invoice.total).quantize(Decimal("0.01"))
    return {
        "po_number": po.po_number,
        "invoiced_before": format_inr(before),
        "invoiced_after": format_inr(after),
        "remaining_after": format_inr((po.total_amount - after).quantize(Decimal("0.01"))),
        "amount_applied": format_inr(invoice.total),
        "_raw_after": after,
    }
