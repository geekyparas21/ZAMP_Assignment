"""Stage 10 - the plain-English output.

Everything here is a template filled from reason codes and evidence that the
deterministic engine already produced. No LLM call is made:

  * it keeps the whole run inside one model call, which matters on a free tier;
  * and more importantly, wording that is generated from the decision cannot
    contradict the decision. A model asked to "explain" an outcome can soften
    it, embellish it, or invent a number. A template cannot.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..money import format_inr
from ..domain import Decision, NormalisedInvoice, PurchaseOrder, Vendor
from .codes import Severity, get
from .engine import blocking_findings

CENT = Decimal("0.01")

_OPENERS = {
    "APPROVED": "Approved for payment.",
    "NEEDS_REVIEW": "Held for review.",
    "REJECTED": "Rejected.",
    "FAILED": "This document could not be processed.",
}


def build_explanation(
    decision: Decision,
    inv: NormalisedInvoice | None,
    vendor: Vendor | None,
    po: PurchaseOrder | None,
) -> str:
    """One paragraph an AP manager can read without any training."""
    parts: list[str] = [_OPENERS.get(decision.outcome, "")]

    who = vendor.legal_name if vendor else (inv.vendor_name if inv else None)
    amount = format_inr(inv.total) if inv and inv.total is not None else None
    number = inv.invoice_number if inv and inv.invoice_number else None

    identity = []
    if number:
        identity.append(f"Invoice {number}")
    if who:
        identity.append(f"from {who}")
    if amount:
        identity.append(f"for INR {amount}")
    if identity:
        sentence = " ".join(identity)
        if po:
            sentence += f", matched to {po.po_number}"
        parts.append(sentence + ".")

    blocking = blocking_findings(decision.findings)
    if blocking:
        parts.append(" ".join(f.message for f in blocking[:3]))
    else:
        notes = [f for f in decision.findings
                 if f.severity == Severity.INFO
                 and f.code not in {"ALL_CHECKS_PASSED", "VENDOR_MATCHED_BY_GSTIN",
                                    "VENDOR_MATCHED_BY_NAME", "PO_MATCHED_EXPLICIT"}]
        if notes:
            parts.append(" ".join(f.message for f in notes[:2]))
        else:
            parts.append("Vendor, purchase order, line items and totals all "
                         "reconciled with no variance, and no duplicate was found.")

    return " ".join(p for p in parts if p).strip()


def build_next_action(
    decision: Decision,
    inv: NormalisedInvoice | None,
    vendor: Vendor | None,
    po: PurchaseOrder | None,
) -> str:
    """The single most useful thing a human should do next."""
    blocking = blocking_findings(decision.findings)
    if blocking:
        return blocking[0].next_action

    if decision.outcome == "APPROVED":
        due = _payment_date(inv, vendor)
        if due:
            return f"Post to the ERP for payment on {due.isoformat()}."
        return "Post to the ERP for payment."
    return get(decision.findings[0].code).next_action if decision.findings else ""


def _payment_date(inv: NormalisedInvoice | None, vendor: Vendor | None) -> date | None:
    """Payment date from the vendor master's terms, not the invoice's own.

    Vendors occasionally state terms more favourable to themselves than the
    ones that were agreed.
    """
    if inv is None or inv.invoice_date is None:
        return None
    days = vendor.net_days if vendor else 30
    return inv.invoice_date + timedelta(days=days)


# ---------------------------------------------------------------------------
# Draft emails
# ---------------------------------------------------------------------------
def build_draft_email(
    decision: Decision,
    inv: NormalisedInvoice | None,
    vendor: Vendor | None,
    po: PurchaseOrder | None,
) -> dict[str, str] | None:
    """A ready-to-send message for anything that needs a human to chase it."""
    if decision.outcome not in ("NEEDS_REVIEW", "REJECTED"):
        return None

    blocking = blocking_findings(decision.findings)
    if not blocking:
        return None

    primary = blocking[0]
    number = (inv.invoice_number if inv and inv.invoice_number else "(no number)")
    who = vendor.legal_name if vendor else (inv.vendor_name if inv else "Supplier")
    internal = primary.code in {
        "PO_INFERRED_AMBIGUOUS", "PO_INFERRED_LOW_CONFIDENCE", "PO_NOT_FOUND",
        "VENDOR_BLOCKED", "VENDOR_PENDING", "VENDOR_NOT_ON_MASTER",
        "VENDOR_BANK_MISMATCH", "QTY_VARIANCE_EXCEEDS_TOLERANCE",
    }

    if internal:
        to = "procurement@acmemanufacturing.example"
        greeting = "Hi team,"
        closing = ("Could you confirm how you would like us to proceed? "
                   "We have put the invoice on hold in the meantime.\n\n"
                   "Thanks,\nAccounts Payable\nAcme Manufacturing Pvt Ltd")
        subject = f"Action needed: invoice {number} from {who} — {primary.reason.title}"
    else:
        to = f"accounts@{_domain(who)}"
        greeting = f"Dear {who},"
        closing = ("Could you confirm, or issue a corrected invoice or credit note "
                   "as appropriate? We will release payment as soon as this is "
                   "resolved.\n\nKind regards,\nAccounts Payable\n"
                   "Acme Manufacturing Pvt Ltd")
        subject = f"Query on invoice {number} — {primary.reason.title}"

    body_lines = [greeting, ""]
    opening = (f"We have received invoice {number}"
               + (f" dated {inv.invoice_date.isoformat()}" if inv and inv.invoice_date else "")
               + (f" for INR {format_inr(inv.total)}" if inv and inv.total is not None else "")
               + (f", raised against {po.po_number}" if po else "")
               + ". We are unable to process it as it stands:")
    body_lines += [opening, ""]
    for finding in blocking[:3]:
        body_lines.append(f"  - {finding.message}")
    body_lines += ["", closing]

    return {"to": to, "subject": subject, "body": "\n".join(body_lines)}


def _domain(name: str) -> str:
    slug = "".join(c for c in name.lower() if c.isalnum() or c.isspace())
    first = slug.split()
    return (first[0] if first else "vendor") + ".example"


