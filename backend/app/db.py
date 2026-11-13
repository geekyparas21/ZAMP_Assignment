"""Database engine and seeding.

One `DATABASE_URL` selects between SQLite locally and hosted Postgres in
production. Seeding is idempotent and runs on boot, so a fresh host comes up
with working reference data and no manual steps.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, delete, select

from .config import DATA_DIR, get_settings
from .models import (LedgerRow, LLMCall, POLineRow, PurchaseOrderRow, Run,
                     RunStep, StoredFile, VendorRow)
from .rules.validation import normalise_invoice_number

log = logging.getLogger(__name__)
_engine = None


def normalise_database_url(url: str) -> str:
    """Make a hosted Postgres URL usable by SQLAlchemy + psycopg 3.

    Neon, Render and Heroku all hand out `postgres://...` or
    `postgresql://...`. SQLAlchemy needs an explicit driver, and psycopg 3 is
    registered as `postgresql+psycopg`. Getting this wrong is the single most
    common first-deploy failure, so it is fixed here rather than in the
    environment variable.
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = normalise_database_url(settings.database_url)
        if url.startswith("sqlite"):
            _engine = create_engine(
                url, connect_args={"check_same_thread": False}, echo=False)
        else:
            # Serverless Postgres suspends when idle; pre-ping replaces a
            # connection that died while the compute was asleep instead of
            # failing the first request after a quiet period.
            _engine = create_engine(
                url, pool_pre_ping=True, pool_recycle=280, pool_size=5,
                max_overflow=5, echo=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests that switch database URLs)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def create_tables() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def ping() -> bool:
    try:
        with Session(get_engine()) as session:
            session.exec(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - reported via /health
        log.error("database ping failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def _date(value: str) -> date | None:
    value = (value or "").strip()
    return datetime.strptime(value[:10], "%Y-%m-%d").date() if value else None


def _money(value: str) -> Decimal:
    return Decimal((value or "0").strip() or "0").quantize(Decimal("0.01"))


def _rows(name: str, data_dir: Path | None = None) -> list[dict]:
    path = (data_dir or DATA_DIR) / name
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def is_seeded(session: Session) -> bool:
    return session.exec(select(VendorRow)).first() is not None


def seed_reference_data(session: Session, data_dir: Path | None = None,
                        force: bool = False) -> dict[str, int]:
    """Load vendors, POs and the historical ledger.

    Idempotent: on boot it does nothing if data is already present. `force`
    is what "Reset demo data" uses - it wipes reference data, runs and
    uploaded files so a demo always starts from a known state.
    """
    if force:
        for model in (POLineRow, PurchaseOrderRow, VendorRow, LedgerRow,
                      RunStep, StoredFile, Run, LLMCall):
            session.exec(delete(model))
        session.commit()
    elif is_seeded(session):
        return {"skipped": 1}

    counts = {"vendors": 0, "purchase_orders": 0, "po_lines": 0, "ledger": 0}

    for row in _rows("vendors.csv", data_dir):
        session.add(VendorRow(
            vendor_id=row["vendor_id"].strip(),
            legal_name=row["legal_name"].strip(),
            aliases=(row["aliases"] or "").strip(),
            gstin=row["gstin"].strip().upper(),
            status=row["status"].strip().lower(),
            payment_terms=row["payment_terms"].strip(),
            bank_account_last4=row["bank_account_last4"].strip(),
        ))
        counts["vendors"] += 1

    for row in _rows("purchase_orders.csv", data_dir):
        session.add(PurchaseOrderRow(
            po_number=row["po_number"].strip(),
            vendor_id=row["vendor_id"].strip(),
            issue_date=_date(row["issue_date"]),
            status=row["status"].strip().lower(),
            currency=row["currency"].strip().upper(),
            total_amount=_money(row["total_amount"]),
            amount_invoiced_to_date=_money(row["amount_invoiced_to_date"]),
            note=(row.get("note") or "").strip(),
        ))
        counts["purchase_orders"] += 1

    for row in _rows("po_lines.csv", data_dir):
        session.add(POLineRow(
            po_number=row["po_number"].strip(),
            line_no=int(row["line_no"]),
            sku=row["sku"].strip(),
            description=row["description"].strip(),
            qty=Decimal(row["qty"]),
            unit_price=Decimal(row["unit_price"]),
            line_amount=_money(row["line_amount"]),
        ))
        counts["po_lines"] += 1

    for row in _rows("invoice_ledger.csv", data_dir):
        number = row["invoice_number"].strip()
        session.add(LedgerRow(
            invoice_id=row["invoice_id"].strip(),
            vendor_id=row["vendor_id"].strip(),
            invoice_number=number,
            invoice_number_normalised=normalise_invoice_number(number) or "",
            invoice_date=_date(row["invoice_date"]),
            currency=row["currency"].strip().upper(),
            total_amount=_money(row["total_amount"]),
            po_number=(row["po_number"] or "").strip() or None,
            outcome=row["outcome"].strip().upper(),
            processed_at=row["processed_at"].strip(),
            file_sha256=(row.get("file_sha256") or "").strip() or None,
            is_seed=True,
        ))
        counts["ledger"] += 1

    session.commit()
    log.info("seeded reference data: %s", counts)
    return counts


def init_db(force_seed: bool = False) -> dict[str, int]:
    create_tables()
    settings = get_settings()
    if not (settings.auto_seed or force_seed):
        return {"skipped": 1}
    with Session(get_engine()) as session:
        return seed_reference_data(session, force=force_seed)
