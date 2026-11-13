"""Plain data structures shared by the pipeline stages.

Deliberately free of database and web-framework imports so the decision engine
can be exercised by tests with nothing but a few dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

Money = Decimal
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@dataclass
class Vendor:
    vendor_id: str
    legal_name: str
    aliases: list[str]
    gstin: str
    status: str                   # approved | blocked | pending
    payment_terms: str            # e.g. "Net 30"
    bank_account_last4: str

    @property
    def all_names(self) -> list[str]:
        return [self.legal_name, *self.aliases]

    @property
    def net_days(self) -> int:
        digits = "".join(c for c in self.payment_terms if c.isdigit())
        return int(digits) if digits else 30


@dataclass
class POLine:
    po_number: str
    line_no: int
    sku: str
    description: str
    qty: Decimal
    unit_price: Decimal
    line_amount: Decimal


@dataclass
class PurchaseOrder:
    po_number: str
    vendor_id: str
    issue_date: date
    status: str                   # open | closed
    currency: str
    total_amount: Money           # GST-inclusive committed value
    amount_invoiced_to_date: Money
    lines: list[POLine] = field(default_factory=list)
    note: str = ""

    @property
    def remaining_balance(self) -> Money:
        return (self.total_amount - self.amount_invoiced_to_date).quantize(Decimal("0.01"))

    @property
    def is_open(self) -> bool:
        return self.status == "open"


@dataclass
class LedgerEntry:
    invoice_id: str
    vendor_id: str
    invoice_number: str
    invoice_date: date | None
    currency: str
    total_amount: Money
    po_number: str | None
    outcome: str
    processed_at: str
    file_sha256: str | None = None


# ---------------------------------------------------------------------------
# Extraction output (what the model returns, before we trust any of it)
# ---------------------------------------------------------------------------
@dataclass
class ExtractedLine:
    description: str | None = None
    hsn: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


@dataclass
class ExtractedInvoice:
    """Raw, untrusted model output, coerced into Python types.

    Every field is optional: a field that is not on the document must arrive
    here as None and be flagged, never guessed at.
    """

    is_invoice: bool = True
    document_type: str = "invoice"
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    bill_to_name: str | None = None
    bill_to_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    po_reference: str | None = None
    currency: str | None = None
    lines: list[ExtractedLine] = field(default_factory=list)
    subtotal: Money | None = None
    tax_total: Money | None = None
    total: Money | None = None
    tax_rate_pct: Decimal | None = None
    tax_inclusive: bool = False
    bank_account_last4: str | None = None
    field_confidence: dict[str, float] = field(default_factory=dict)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Normalised invoice (after validation stage)
# ---------------------------------------------------------------------------
@dataclass
class NormalisedInvoice:
    raw: ExtractedInvoice
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    bill_to_name: str | None = None
    bill_to_gstin: str | None = None
    invoice_number: str | None = None
    invoice_number_normalised: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_reference: str | None = None
    po_reference_normalised: str | None = None
    currency: str = "INR"
    lines: list[ExtractedLine] = field(default_factory=list)
    subtotal: Money | None = None
    tax_total: Money | None = None
    total: Money | None = None
    tax_rate_pct: Decimal | None = None
    tax_inclusive: bool = False
    bank_account_last4: str | None = None
    #: min(model confidence, validation-derived confidence) per field
    confidence: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage results
# ---------------------------------------------------------------------------
@dataclass
class VendorMatch:
    vendor: Vendor | None
    method: str                   # gstin | name | none
    score: float
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class POCandidate:
    po: PurchaseOrder
    score: float
    components: dict[str, float]
    rationale: str


@dataclass
class POMatch:
    po: PurchaseOrder | None
    method: str                   # explicit | inferred | none
    score: float
    candidates: list[POCandidate] = field(default_factory=list)


@dataclass
class LineComparison:
    description: str
    po_description: str | None
    invoice_qty: Decimal | None
    po_qty: Decimal | None
    invoice_unit_price: Decimal | None
    po_unit_price: Decimal | None
    price_variance_pct: Decimal | None
    qty_variance_pct: Decimal | None
    invoice_amount: Decimal | None
    matched: bool
    status: str                   # ok | price_variance | qty_variance | unmatched


@dataclass
class Reconciliation:
    comparisons: list[LineComparison] = field(default_factory=list)
    expected_amount: Money | None = None
    actual_amount: Money | None = None
    difference: Money | None = None
    variance_pct: Decimal | None = None
    band_applied: str | None = None
    within_tolerance: bool | None = None


@dataclass
class Decision:
    outcome: str                  # APPROVED | NEEDS_REVIEW | REJECTED | FAILED
    findings: list[Any] = field(default_factory=list)   # list[Finding]
    explanation: str = ""
    next_action: str = ""
    draft_email: dict[str, str] | None = None

    @property
    def top_reason(self) -> str | None:
        return self.findings[0].code if self.findings else None
