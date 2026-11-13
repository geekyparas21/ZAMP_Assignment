"""Stages 4-10 as one deterministic function.

Everything from validation to the drafted email happens here, with no network
access and no model call. The pipeline orchestrator adds stages 1-3 (ingest,
document analysis, extraction) around it; the tests call it directly, so what
the tests prove is what production runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

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


#: Called as each stage finishes: (stage, findings, detail payload).
StageCallback = Callable[[str, list[Finding], dict[str, Any]], None]


def evaluate(
    extracted: ExtractedInvoice,
    reference: ReferenceData,
    file_sha256: str | None = None,
    rules: Rules | None = None,
    prior_findings: list[Finding] | None = None,
    on_stage: StageCallback | None = None,
) -> EvaluationResult:
    """Run every deterministic check and produce one decision.

    `prior_findings` carries anything the earlier stages already observed -
    for example that the document was a scan - so it lands in the same
    decision and the same audit trail.

    `on_stage` fires as each stage completes, which is how the live run view
    streams progress. It is the same code path the tests take, so what the UI
    shows cannot drift from what the tests prove.
    """
    rules = rules or get_rules()
    findings: list[Finding] = list(prior_findings or [])
    stages: dict[str, list[Finding]] = {}

    def record(stage: str, produced: list[Finding],
               detail: dict[str, Any] | None = None) -> None:
        stages[stage] = produced
        findings.extend(produced)
        if on_stage is not None:
            on_stage(stage, produced, detail or {})

    # --- Not an invoice: stop before pretending to reconcile one -----------
    if not extracted.is_invoice:
        produced = [Finding("NOT_AN_INVOICE", "extraction",
                            {"document_type": extracted.document_type or "document"})]
        record("validation", produced,
               {"document_type": extracted.document_type, "is_invoice": False})
        for skipped in ("vendor", "duplicates", "po_matching", "reconciliation"):
            record(skipped, [], {"skipped": True,
                                 "reason": "the document is not an invoice"})
        decision = decide(findings)
        decision.explanation = build_explanation(decision, None, None, None)
        decision.next_action = build_next_action(decision, None, None, None)
        decision.draft_email = build_draft_email(decision, None, None, None)
        record("decision", [], {"outcome": decision.outcome})
        return EvaluationResult(decision, None, None, None, None,
                                decision.findings, stages)

    # --- 4. Validation and normalisation -----------------------------------
    invoice, produced = validate_and_normalise(extracted, rules)
    record("validation", produced, _validation_detail(invoice))

    # --- 5. Vendor verification --------------------------------------------
    vendor_match, produced = match_vendor(invoice, reference.vendors, rules)
    record("vendor", produced, _vendor_detail(vendor_match))
    vendor = vendor_match.vendor

    # --- 6. Duplicate detection --------------------------------------------
    produced = detect_duplicates(invoice, vendor, reference.ledger, file_sha256, rules)
    record("duplicates", produced, {
        "ledger_entries_searched": len(reference.ledger),
        "file_sha256": (file_sha256 or "")[:16],
        "normalised_invoice_number": invoice.invoice_number_normalised,
    })

    # --- 7. PO matching -----------------------------------------------------
    po_match, produced = match_po(invoice, vendor, reference.purchase_orders, rules)
    record("po_matching", produced, _po_detail(po_match))

    # --- 8. Reconciliation ---------------------------------------------------
    recon, produced = reconcile(invoice, po_match.po, rules)
    record("reconciliation", produced, _reconciliation_detail(recon))

    # --- 9. Decision ---------------------------------------------------------
    decision = decide(findings)

    # --- 10. Output ----------------------------------------------------------
    decision.explanation = build_explanation(decision, invoice, vendor, po_match.po)
    decision.next_action = build_next_action(decision, invoice, vendor, po_match.po)
    decision.draft_email = build_draft_email(decision, invoice, vendor, po_match.po)
    record("decision", [], {
        "outcome": decision.outcome,
        "top_reason": decision.top_reason,
        "rules_evaluated": len(decision.findings),
    })

    return EvaluationResult(decision, invoice, vendor_match, po_match, recon,
                            decision.findings, stages)


# ---------------------------------------------------------------------------
# Per-stage detail payloads - what an expanded stage shows in the UI
# ---------------------------------------------------------------------------
def _validation_detail(invoice: NormalisedInvoice) -> dict[str, Any]:
    return {
        "vendor_name": invoice.vendor_name,
        "vendor_gstin": invoice.vendor_gstin,
        "invoice_number": invoice.invoice_number,
        "invoice_number_normalised": invoice.invoice_number_normalised,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "po_reference": invoice.po_reference,
        "po_reference_normalised": invoice.po_reference_normalised,
        "currency": invoice.currency,
        "subtotal": format_inr(invoice.subtotal),
        "tax_total": format_inr(invoice.tax_total),
        "total": format_inr(invoice.total),
        "tax_inclusive": invoice.tax_inclusive,
        "bank_account_last4": invoice.bank_account_last4,
        "confidence": invoice.confidence,
        "line_count": len(invoice.lines),
        "lines": [
            {
                "description": line.description,
                "qty": str(line.qty) if line.qty is not None else None,
                "unit": line.unit,
                "unit_price": format_inr(line.unit_price),
                "amount": format_inr(line.amount),
            }
            for line in invoice.lines
        ],
    }


def _vendor_detail(match: VendorMatch) -> dict[str, Any]:
    vendor = match.vendor
    return {
        "method": match.method,
        "score": round(match.score, 4),
        "matched": vendor is not None,
        "vendor_id": vendor.vendor_id if vendor else None,
        "legal_name": vendor.legal_name if vendor else None,
        "status": vendor.status if vendor else None,
        "payment_terms": vendor.payment_terms if vendor else None,
        "master_bank_last4": vendor.bank_account_last4 if vendor else None,
        "candidates": match.candidates,
    }


def _po_detail(match: POMatch) -> dict[str, Any]:
    po = match.po
    return {
        "method": match.method,
        "score": round(match.score, 4),
        "po_number": po.po_number if po else None,
        "po_total": format_inr(po.total_amount) if po else None,
        "po_invoiced_to_date": format_inr(po.amount_invoiced_to_date) if po else None,
        "po_remaining": format_inr(po.remaining_balance) if po else None,
        "candidates": [
            {
                "po_number": candidate.po.po_number,
                "score": round(candidate.score, 4),
                "components": candidate.components,
                "rationale": candidate.rationale,
                "po_total": format_inr(candidate.po.total_amount),
                "po_remaining": format_inr(candidate.po.remaining_balance),
            }
            for candidate in match.candidates
        ],
    }


def _reconciliation_detail(recon: Reconciliation) -> dict[str, Any]:
    return {
        "expected_amount": format_inr(recon.expected_amount),
        "actual_amount": format_inr(recon.actual_amount),
        "difference": format_inr(recon.difference),
        "variance_pct": (str(recon.variance_pct)
                         if recon.variance_pct is not None else None),
        "band_applied": recon.band_applied,
        "within_tolerance": recon.within_tolerance,
        "lines": [
            {
                "description": c.description,
                "po_description": c.po_description,
                "invoice_qty": str(c.invoice_qty) if c.invoice_qty is not None else None,
                "po_qty": str(c.po_qty) if c.po_qty is not None else None,
                "invoice_unit_price": format_inr(c.invoice_unit_price),
                "po_unit_price": format_inr(c.po_unit_price),
                "price_variance_pct": (str(c.price_variance_pct)
                                       if c.price_variance_pct is not None else None),
                "qty_variance_pct": (str(c.qty_variance_pct)
                                     if c.qty_variance_pct is not None else None),
                "invoice_amount": format_inr(c.invoice_amount),
                "matched": c.matched,
                "status": c.status,
            }
            for c in recon.comparisons
        ],
    }


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
