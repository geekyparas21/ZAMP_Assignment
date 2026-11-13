"""Loading reference data from the seed CSVs.

Used to seed the database on boot, and directly by the tests so the decision
engine can be exercised against the same vendors and POs the live app uses.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .config import DATA_DIR
from .domain import LedgerEntry, POLine, PurchaseOrder, Vendor


def _date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _money(text: str) -> Decimal:
    return Decimal((text or "0").strip() or "0").quantize(Decimal("0.01"))


def load_vendors(data_dir: Path | None = None) -> list[Vendor]:
    path = (data_dir or DATA_DIR) / "vendors.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [
            Vendor(
                vendor_id=row["vendor_id"].strip(),
                legal_name=row["legal_name"].strip(),
                aliases=[a.strip() for a in (row["aliases"] or "").split("|") if a.strip()],
                gstin=row["gstin"].strip().upper(),
                status=row["status"].strip().lower(),
                payment_terms=row["payment_terms"].strip(),
                bank_account_last4=row["bank_account_last4"].strip(),
            )
            for row in csv.DictReader(fh)
        ]


def load_purchase_orders(data_dir: Path | None = None) -> list[PurchaseOrder]:
    base = data_dir or DATA_DIR
    lines_by_po: dict[str, list[POLine]] = {}
    with (base / "po_lines.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            lines_by_po.setdefault(row["po_number"].strip(), []).append(POLine(
                po_number=row["po_number"].strip(),
                line_no=int(row["line_no"]),
                sku=row["sku"].strip(),
                description=row["description"].strip(),
                qty=Decimal(row["qty"]),
                unit_price=Decimal(row["unit_price"]),
                line_amount=_money(row["line_amount"]),
            ))

    with (base / "purchase_orders.csv").open(encoding="utf-8-sig", newline="") as fh:
        return [
            PurchaseOrder(
                po_number=row["po_number"].strip(),
                vendor_id=row["vendor_id"].strip(),
                issue_date=_date(row["issue_date"]),
                status=row["status"].strip().lower(),
                currency=row["currency"].strip().upper(),
                total_amount=_money(row["total_amount"]),
                amount_invoiced_to_date=_money(row["amount_invoiced_to_date"]),
                lines=lines_by_po.get(row["po_number"].strip(), []),
                note=(row.get("note") or "").strip(),
            )
            for row in csv.DictReader(fh)
        ]


def load_ledger(data_dir: Path | None = None) -> list[LedgerEntry]:
    path = (data_dir or DATA_DIR) / "invoice_ledger.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [
            LedgerEntry(
                invoice_id=row["invoice_id"].strip(),
                vendor_id=row["vendor_id"].strip(),
                invoice_number=row["invoice_number"].strip(),
                invoice_date=_date(row["invoice_date"]),
                currency=row["currency"].strip().upper(),
                total_amount=_money(row["total_amount"]),
                po_number=(row["po_number"] or "").strip() or None,
                outcome=row["outcome"].strip().upper(),
                processed_at=row["processed_at"].strip(),
                file_sha256=(row.get("file_sha256") or "").strip() or None,
            )
            for row in csv.DictReader(fh)
        ]
