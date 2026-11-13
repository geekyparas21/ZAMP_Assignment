"""Stage 7 - purchase order matching.

If the invoice quotes a PO, that reference is used. If it does not - which is
common, and is one of the scenarios this system exists to handle - candidates
are inferred from the vendor's open POs and scored on how well the amount and
the line items line up.

Inference never silently guesses. A candidate must clear an absolute score
AND beat the runner-up by a margin; otherwise the invoice goes to a human with
the candidates and their scores on screen.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import Rules
from ..money import format_inr
from ..domain import NormalisedInvoice, POCandidate, POMatch, PurchaseOrder, Vendor
from .codes import Finding
from .validation import normalise_po_reference
from .vendor import name_similarity

STAGE = "po_matching"
CENT = Decimal("0.01")


def match_po(
    inv: NormalisedInvoice,
    vendor: Vendor | None,
    purchase_orders: list[PurchaseOrder],
    rules: Rules,
) -> tuple[POMatch, list[Finding]]:
    findings: list[Finding] = []

    # --- 1. Explicit reference on the document -----------------------------
    if inv.po_reference_normalised:
        wanted = inv.po_reference_normalised
        for po in purchase_orders:
            if normalise_po_reference(po.po_number) == wanted:
                findings.append(Finding("PO_MATCHED_EXPLICIT", STAGE, {
                    "po_reference": inv.po_reference, "po_number": po.po_number,
                }))
                findings.extend(_po_sanity_checks(inv, vendor, po))
                return POMatch(po, "explicit", 1.0), findings

    # --- 2. Inference from the vendor's open POs ---------------------------
    candidates = _rank_candidates(inv, vendor, purchase_orders)
    detail = ", ".join(f"{c.po.po_number} ({c.score:.0%})" for c in candidates[:3])

    if not candidates:
        # Two different statements, and a reviewer needs to know which: a PO
        # number that does not exist is a data error, whereas no PO at all may
        # simply be a purchase nobody raised one for.
        if inv.po_reference_normalised:
            findings.append(Finding("PO_NOT_FOUND", STAGE, {
                "po_reference": inv.po_reference or "(none quoted)",
                "normalised": inv.po_reference_normalised or "-",
            }))
        else:
            findings.append(Finding("NO_PO_FOUND", STAGE, {
                "search_scope": (f"for {vendor.legal_name}" if vendor is not None
                                 else "on the system (the vendor is unknown, so "
                                      "there are no POs to search)"),
                "vendor_known": vendor is not None,
                "invoice_total": format_inr(inv.total),
            }))
        return POMatch(None, "none", 0.0, []), findings

    min_score = rules.number("po_matching.inference_min_score")
    min_margin = rules.number("po_matching.inference_min_margin")
    best = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    margin = best.score - runner_up

    if best.score < min_score:
        findings.append(Finding("PO_INFERRED_LOW_CONFIDENCE", STAGE, {
            "po_number": best.po.po_number, "score": best.score,
            "threshold": min_score, "detail": detail,
        }))
        return POMatch(None, "none", best.score, candidates), findings

    if margin < min_margin:
        findings.append(Finding("PO_INFERRED_AMBIGUOUS", STAGE, {
            "candidate_count": len(candidates), "detail": detail,
            "margin": margin, "required_margin": min_margin,
        }))
        return POMatch(None, "none", best.score, candidates), findings

    findings.append(Finding("PO_INFERRED_HIGH_CONFIDENCE", STAGE, {
        "po_number": best.po.po_number, "score": best.score,
        "reason": best.rationale, "margin": margin, "detail": detail,
    }))
    findings.extend(_po_sanity_checks(inv, vendor, best.po))
    return POMatch(best.po, "inferred", best.score, candidates), findings


def _rank_candidates(
    inv: NormalisedInvoice, vendor: Vendor | None, purchase_orders: list[PurchaseOrder]
) -> list[POCandidate]:
    """Score every open PO for this vendor on amount and line-item similarity."""
    if vendor is None or inv.total is None:
        return []

    pool = [po for po in purchase_orders
            if po.vendor_id == vendor.vendor_id and po.is_open]
    if not pool:
        return []

    newest = max((po.issue_date for po in pool), default=None)
    oldest = min((po.issue_date for po in pool), default=None)
    span = (newest - oldest).days if newest and oldest else 0

    candidates: list[POCandidate] = []
    for po in pool:
        amount_score = _amount_similarity(inv.total, po)
        line_score = _line_similarity(inv, po)
        comparable = _lines_are_comparable(inv, po)
        if span > 0 and newest:
            recency = 1.0 - ((newest - po.issue_date).days / span)
        else:
            recency = 1.0
        components = {
            "amount_similarity": round(amount_score, 4),
            "line_similarity": round(line_score, 4) if comparable else None,
            "recency": round(recency, 4),
        }
        # Where there is no line detail on either side, line similarity is
        # unknown, not zero. Scoring an unknown as zero would permanently cap
        # a value-only PO at 0.65 and make it uninferrable however exactly its
        # amount matched, so its weight is redistributed over what we do know.
        if comparable:
            score = amount_score * 0.50 + line_score * 0.35 + recency * 0.15
        else:
            score = (amount_score * 0.50 + recency * 0.15) / 0.65
        candidates.append(POCandidate(
            po=po, score=round(score, 4), components=components,
            rationale=_rationale(amount_score, line_score if comparable else None),
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _amount_similarity(invoice_total: Decimal, po: PurchaseOrder) -> float:
    """How well the invoice total fits the PO's remaining balance.

    Measured against the remaining balance rather than the PO total, so a PO
    that is already half-billed still scores well for the invoice that bills
    the other half.
    """
    remaining = po.remaining_balance
    if remaining <= 0:
        return 0.0
    if invoice_total <= remaining:
        # Billing part of what is left is legitimate; billing all of it is the
        # strongest possible signal.
        return float(invoice_total / remaining)
    overage = float((invoice_total - remaining) / remaining)
    return max(0.0, 1.0 - overage)


def _lines_are_comparable(inv: NormalisedInvoice, po: PurchaseOrder) -> bool:
    """True only when both sides actually carry descriptions to compare."""
    return bool(po.lines) and any(l.description for l in inv.lines)


def _line_similarity(inv: NormalisedInvoice, po: PurchaseOrder) -> float:
    """Mean best-match description similarity across the invoice's lines."""
    descriptions = [l.description for l in inv.lines if l.description]
    if not descriptions or not po.lines:
        return 0.0
    scores = []
    for text in descriptions:
        scores.append(max(
            max(name_similarity(text, line.description),
                name_similarity(text, line.sku))
            for line in po.lines
        ))
    return sum(scores) / len(scores)


def _rationale(amount_score: float, line_score: float | None) -> str:
    bits = []
    if amount_score > 0.95:
        bits.append("the amount matches the remaining balance almost exactly")
    elif amount_score > 0.7:
        bits.append("the amount fits within the remaining balance")
    if line_score is None:
        bits.append("the PO carries no line detail, so only the amount could "
                    "be compared")
    elif line_score > 0.85:
        bits.append("the line items match the PO lines")
    elif line_score > 0.5:
        bits.append("the line items are similar to the PO lines")
    return " and ".join(bits) if bits else "it is the only open PO for this vendor"


def _po_sanity_checks(
    inv: NormalisedInvoice, vendor: Vendor | None, po: PurchaseOrder
) -> list[Finding]:
    findings: list[Finding] = []

    if po.status == "closed":
        findings.append(Finding("PO_CLOSED", STAGE, {
            "po_number": po.po_number, "status": po.status,
            "invoiced": format_inr(po.amount_invoiced_to_date),
            "po_total": format_inr(po.total_amount),
        }))

    if vendor is not None and po.vendor_id != vendor.vendor_id:
        findings.append(Finding("PO_VENDOR_MISMATCH", STAGE, {
            "po_number": po.po_number, "po_vendor": po.vendor_id,
            "invoice_vendor": vendor.legal_name,
        }))

    if inv.currency and po.currency and inv.currency.upper() != po.currency.upper():
        findings.append(Finding("CURRENCY_MISMATCH", STAGE, {
            "invoice_currency": inv.currency, "po_currency": po.currency,
            "po_number": po.po_number,
        }))
    return findings


