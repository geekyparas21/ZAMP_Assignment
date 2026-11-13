"""Database tables.

Everything that must survive a restart lives here, including the uploaded PDF
bytes. Free hosting tiers have ephemeral filesystems: anything written to local
disk is gone on the next deploy or idle spin-down, which would empty the
dashboard of past runs. So nothing the app needs is kept on disk.

The same models run on SQLite locally and Postgres in production; column types
are chosen to behave identically on both.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import JSON, Column, LargeBinary, Numeric, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money_column() -> Column:
    # Numeric keeps money exact on Postgres; SQLite maps it through Decimal
    # rather than float, which is what matters for reconciliation arithmetic.
    return Column(Numeric(14, 2))


# ---------------------------------------------------------------------------
# Reference data (seeded from data/*.csv, idempotently, on boot)
# ---------------------------------------------------------------------------
class VendorRow(SQLModel, table=True):
    __tablename__ = "vendors"

    vendor_id: str = Field(primary_key=True)
    legal_name: str
    aliases: str = ""
    gstin: str = Field(index=True)
    status: str = "approved"
    payment_terms: str = "Net 30"
    bank_account_last4: str = ""


class PurchaseOrderRow(SQLModel, table=True):
    __tablename__ = "purchase_orders"

    po_number: str = Field(primary_key=True)
    vendor_id: str = Field(index=True)
    issue_date: Optional[date] = None
    status: str = "open"
    currency: str = "INR"
    total_amount: Decimal = Field(default=Decimal("0.00"), sa_column=_money_column())
    amount_invoiced_to_date: Decimal = Field(default=Decimal("0.00"),
                                             sa_column=_money_column())
    note: str = ""


class POLineRow(SQLModel, table=True):
    __tablename__ = "po_lines"

    id: Optional[int] = Field(default=None, primary_key=True)
    po_number: str = Field(index=True)
    line_no: int = 1
    sku: str = ""
    description: str = ""
    qty: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(14, 3)))
    unit_price: Decimal = Field(default=Decimal("0.00"), sa_column=_money_column())
    line_amount: Decimal = Field(default=Decimal("0.00"), sa_column=_money_column())


class LedgerRow(SQLModel, table=True):
    """Invoices already processed - what duplicate detection searches."""

    __tablename__ = "invoice_ledger"

    invoice_id: str = Field(primary_key=True)
    vendor_id: str = Field(index=True)
    invoice_number: str = ""
    invoice_number_normalised: str = Field(default="", index=True)
    invoice_date: Optional[date] = None
    currency: str = "INR"
    total_amount: Decimal = Field(default=Decimal("0.00"), sa_column=_money_column())
    po_number: Optional[str] = None
    outcome: str = "APPROVED"
    processed_at: str = ""
    file_sha256: Optional[str] = Field(default=None, index=True)
    run_id: Optional[str] = Field(default=None, index=True)
    #: True for rows that came from the seed file rather than a real run, so
    #: "Reset demo data" can restore exactly the starting state.
    is_seed: bool = False


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    filename: str = ""
    sample_key: Optional[str] = None
    file_sha256: str = Field(default="", index=True)
    size_bytes: int = 0
    page_count: int = 0
    is_scanned: bool = False

    #: running | completed | failed
    status: str = Field(default="running", index=True)
    #: APPROVED | NEEDS_REVIEW | REJECTED | FAILED
    outcome: Optional[str] = Field(default=None, index=True)
    top_reason: Optional[str] = None

    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    total_amount: Optional[Decimal] = Field(default=None, sa_column=_money_column())
    currency: str = "INR"
    po_number: Optional[str] = None

    explanation: str = Field(default="", sa_column=Column(Text))
    next_action: str = Field(default="", sa_column=Column(Text))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))

    llm_model: Optional[str] = None
    llm_tokens: int = 0
    llm_calls: int = 0

    findings: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    extraction: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    reconciliation: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    draft_email: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    po_candidates: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    confidence: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    ledger_effect: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))


class RunStep(SQLModel, table=True):
    """One pipeline stage, persisted so a past run replays exactly as it ran."""

    __tablename__ = "run_steps"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    seq: int = 0
    stage: str = ""
    title: str = ""
    #: pending | running | passed | warning | failed | skipped
    status: str = "pending"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    summary: str = Field(default="", sa_column=Column(Text))
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class StoredFile(SQLModel, table=True):
    """The uploaded PDF itself, kept in the database rather than on disk."""

    __tablename__ = "stored_files"

    run_id: str = Field(primary_key=True)
    filename: str = ""
    content_type: str = "application/pdf"
    size_bytes: int = 0
    content: bytes = Field(sa_column=Column(LargeBinary))


class LLMCall(SQLModel, table=True):
    """One model call, for the quota counter shown before a demo."""

    __tablename__ = "llm_calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    model: str = ""
    attempts: int = 1
    prompt_tokens: int = 0
    response_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    ok: bool = True
    error_code: Optional[str] = None
