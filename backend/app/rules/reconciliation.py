"""Stage 8 - reconciliation of the invoice against the matched PO.

Two levels, because they catch different things:

  * Total level, against the PO's REMAINING balance. This is what makes split
    invoicing work: the third invoice on a PO is compared with what is left,
    not with the PO's original value.
  * Line level, on pre-tax unit prices and quantities. A total-level check
    alone would hide one inflated SKU inside an otherwise correct invoice.

Under-billing is never a breach - it is partial billing, and carries no risk
to the buyer.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import Rules
from ..money import format_inr
from ..domain import (LineComparison, NormalisedInvoice, PurchaseOrder,
                      Reconciliation)
from .codes import Finding
from .vendor import name_similarity

STAGE = "reconciliation"
CENT = Decimal("0.01")

#: Below this, two descriptions are different items rather than one item
#: written two ways.
_LINE_MATCH_FLOOR = 0.55


def reconcile(
    inv: NormalisedInvoice, po: PurchaseOrder | None, rules: Rules
) -> tuple[Reconciliation, list[Finding]]:
    if po is None:
        return Reconciliation(), []

    findings: list[Finding] = []
    recon = Reconciliation()

    # --- totals -------------------------------------------------------------
    remaining = po.remaining_balance
    recon.expected_amount = remaining
    recon.actual_amount = inv.total
    band, band_label = rules.tolerance_band(remaining)
    recon.band_applied = band_label

    if inv.total is not None:
        difference = (inv.total - remaining).quantize(CENT)
        recon.difference = difference
        recon.variance_pct = (
            (difference / remaining * 100).quantize(Decimal("0.01"))
            if remaining else Decimal("0.00")
        )

        if difference > band:
            recon.within_tolerance = False
            # Over the remaining balance is over-billing, which is a different
            # and more serious statement than "the total is a bit off".
            if po.amount_invoiced_to_date > 0:
                findings.append(Finding("PO_OVERBILLED", STAGE, {
                    "po_number": po.po_number, "po_total": format_inr(po.total_amount),
                    "invoiced": format_inr(po.amount_invoiced_to_date),
                    "remaining": format_inr(remaining), "invoice_total": format_inr(inv.total),
                    "overage": format_inr(difference), "band": band_label,
                }))
            else:
                findings.append(Finding("TOTAL_VARIANCE_EXCEEDS_TOLERANCE", STAGE, {
                    "expected": format_inr(remaining), "actual": format_inr(inv.total),
                    "difference": format_inr(difference), "pct": recon.variance_pct,  # sanitised on write; kept exact here
                    "band": band_label, "po_number": po.po_number,
                }))
        elif difference > 0:
            recon.within_tolerance = True
            findings.append(Finding("TOTAL_VARIANCE_WITHIN_TOLERANCE", STAGE, {
                "expected": format_inr(remaining), "actual": format_inr(inv.total),
                "difference": format_inr(difference), "pct": recon.variance_pct,  # sanitised on write; kept exact here
                "band": band_label, "po_number": po.po_number,
            }))
        else:
            recon.within_tolerance = True
            remaining_after = (remaining - inv.total).quantize(CENT)
            if remaining_after > band:
                findings.append(Finding("PARTIAL_INVOICE_AGAINST_PO", STAGE, {
                    "po_number": po.po_number, "invoice_total": format_inr(inv.total),
                    "po_total": format_inr(po.total_amount),
                    "remaining_after": format_inr(remaining_after),
                }))

    # --- lines --------------------------------------------------------------
    price_pct = rules.decimal("tolerance.line_unit_price_variance_pct")
    qty_pct = rules.decimal("tolerance.line_quantity_variance_pct")
    price_breaches: list[str] = []
    qty_breaches: list[str] = []
    unmatched: list[str] = []
    unmatched_amount = Decimal("0.00")

    for line in inv.lines:
        po_line = _best_po_line(line.description, po)
        comparison = LineComparison(
            description=line.description or "(no description)",
            po_description=po_line.description if po_line else None,
            invoice_qty=line.qty, po_qty=po_line.qty if po_line else None,
            invoice_unit_price=line.unit_price,
            po_unit_price=po_line.unit_price if po_line else None,
            price_variance_pct=None, qty_variance_pct=None,
            invoice_amount=line.amount, matched=po_line is not None, status="ok",
        )

        if po_line is None:
            comparison.status = "unmatched"
            unmatched.append(f"{comparison.description} ({format_inr(line.amount)})")
            unmatched_amount += line.amount or Decimal("0.00")
            recon.comparisons.append(comparison)
            continue

        if line.unit_price is not None and po_line.unit_price:
            variance = ((line.unit_price - po_line.unit_price)
                        / po_line.unit_price * 100).quantize(Decimal("0.01"))
            comparison.price_variance_pct = variance
            if variance > price_pct:
                comparison.status = "price_variance"
                price_breaches.append(
                    f"{comparison.description}: PO {format_inr(po_line.unit_price)} vs "
                    f"invoiced {format_inr(line.unit_price)} (+{variance}%)")

        if line.qty is not None and po_line.qty:
            variance = ((line.qty - po_line.qty) / po_line.qty * 100).quantize(
                Decimal("0.01"))
            comparison.qty_variance_pct = variance
            if variance > qty_pct:
                if comparison.status == "ok":
                    comparison.status = "qty_variance"
                qty_breaches.append(
                    f"{comparison.description}: PO {po_line.qty} vs "
                    f"invoiced {line.qty} (+{variance}%)")

        recon.comparisons.append(comparison)

    if price_breaches:
        findings.append(Finding("PRICE_VARIANCE_EXCEEDS_TOLERANCE", STAGE, {
            "line_count": len(price_breaches), "detail": "; ".join(price_breaches),
            "threshold_pct": f"{price_pct.normalize():f}", "lines": price_breaches,
        }))
    if qty_breaches:
        findings.append(Finding("QTY_VARIANCE_EXCEEDS_TOLERANCE", STAGE, {
            "line_count": len(qty_breaches), "detail": "; ".join(qty_breaches),
            "threshold_pct": f"{qty_pct.normalize():f}", "lines": qty_breaches,
        }))

    # An unmatched line is only escalated when it is material. Incidental
    # charges (freight, handling) below the tolerance band are noted on the
    # comparison table but do not force a human review.
    if unmatched and rules.boolean("po_matching.unmatched_line_uses_total_tolerance"):
        if unmatched_amount > band:
            findings.append(Finding("LINE_NOT_ON_PO", STAGE, {
                "line_count": len(unmatched), "detail": "; ".join(unmatched),
                "po_number": po.po_number, "amount": format_inr(unmatched_amount),
            }))
    elif unmatched:
        findings.append(Finding("LINE_NOT_ON_PO", STAGE, {
            "line_count": len(unmatched), "detail": "; ".join(unmatched),
            "po_number": po.po_number, "amount": format_inr(unmatched_amount),
        }))

    return recon, findings


def _best_po_line(description: str | None, po: PurchaseOrder):
    if not description:
        return None
    best, best_score = None, 0.0
    for line in po.lines:
        score = max(name_similarity(description, line.description),
                    name_similarity(description, line.sku))
        if score > best_score:
            best, best_score = line, score
    return best if best_score >= _LINE_MATCH_FLOOR else None


