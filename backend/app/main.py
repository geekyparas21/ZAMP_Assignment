"""The web service.

One deployable unit: FastAPI serves the JSON API and, once it exists, the
built frontend as static files from the same origin. No separate frontend
host, no CORS to configure, one URL to give an interviewer.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlmodel import Session, select

load_dotenv()

from .config import SAMPLES_DIR, get_rules, get_settings  # noqa: E402
from .db import get_engine, get_session, init_db, ping, seed_reference_data  # noqa: E402
from .models import (LedgerRow, LLMCall, POLineRow, PurchaseOrderRow,  # noqa: E402
                     VendorRow)
from .money import format_inr  # noqa: E402
from .pipeline.events import bus  # noqa: E402
from .rules.codes import all_codes  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(
    title="AI Invoice Processing Pipeline",
    description="From invoice PDF to a reasoned, auditable AP decision.",
    version="0.5.0",
)

from .api_runs import router as runs_router  # noqa: E402

app.include_router(runs_router)


@app.on_event("startup")
async def on_startup() -> None:
    import asyncio

    # Worker threads post live run events into this loop.
    bus.bind_loop(asyncio.get_running_loop())

    settings = get_settings()
    log.info("starting up (env=%s, db=%s)", settings.app_env,
             settings.database_url.split("@")[-1][:60])
    try:
        counts = init_db()
        log.info("database ready: %s", counts)
    except Exception as exc:
        # A failed seed must not stop the service from booting - /health will
        # report the problem rather than the host showing a dead port.
        log.exception("database initialisation failed: %s", exc)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> JSONResponse:
    """Liveness + readiness. Safe to ping to keep a free host awake."""
    settings = get_settings()
    database_ok = ping()
    body: dict[str, Any] = {
        "status": "ok" if database_ok else "degraded",
        "env": settings.app_env,
        "database": "ok" if database_ok else "unreachable",
        "database_kind": ("postgres" if "postgres" in settings.database_url
                          else "sqlite"),
        "llm_configured": settings.llm_configured,
        "model": settings.gemini_model,
        "frontend_built": FRONTEND_DIST.exists(),
        "time": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(body, status_code=200 if database_ok else 503)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@app.get("/api/reference/vendors")
def list_vendors(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(VendorRow).order_by(VendorRow.vendor_id)).all()
    return [
        {
            "vendor_id": row.vendor_id,
            "legal_name": row.legal_name,
            "aliases": [a for a in row.aliases.split("|") if a],
            "gstin": row.gstin,
            "status": row.status,
            "payment_terms": row.payment_terms,
            "bank_account_last4": row.bank_account_last4,
        }
        for row in rows
    ]


@app.get("/api/reference/purchase-orders")
def list_purchase_orders(session: Session = Depends(get_session)) -> list[dict]:
    pos = session.exec(
        select(PurchaseOrderRow).order_by(PurchaseOrderRow.po_number)).all()
    lines = session.exec(select(POLineRow)).all()
    by_po: dict[str, list[POLineRow]] = {}
    for line in lines:
        by_po.setdefault(line.po_number, []).append(line)

    out = []
    for po in pos:
        remaining = po.total_amount - po.amount_invoiced_to_date
        out.append({
            "po_number": po.po_number,
            "vendor_id": po.vendor_id,
            "issue_date": po.issue_date.isoformat() if po.issue_date else None,
            "status": po.status,
            "currency": po.currency,
            "total_amount": str(po.total_amount),
            "total_amount_display": format_inr(po.total_amount),
            "amount_invoiced_to_date": str(po.amount_invoiced_to_date),
            "amount_invoiced_display": format_inr(po.amount_invoiced_to_date),
            "remaining_balance": str(remaining),
            "remaining_display": format_inr(remaining),
            "note": po.note,
            "lines": [
                {
                    "line_no": line.line_no, "sku": line.sku,
                    "description": line.description, "qty": str(line.qty),
                    "unit_price": str(line.unit_price),
                    "unit_price_display": format_inr(line.unit_price),
                    "line_amount_display": format_inr(line.line_amount),
                }
                for line in sorted(by_po.get(po.po_number, []),
                                   key=lambda x: x.line_no)
            ],
        })
    return out


@app.get("/api/reference/ledger")
def list_ledger(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(
        select(LedgerRow).order_by(LedgerRow.invoice_date.desc())).all()
    return [
        {
            "invoice_id": row.invoice_id,
            "vendor_id": row.vendor_id,
            "invoice_number": row.invoice_number,
            "invoice_date": row.invoice_date.isoformat() if row.invoice_date else None,
            "total_amount_display": format_inr(row.total_amount),
            "po_number": row.po_number,
            "outcome": row.outcome,
            "processed_at": row.processed_at,
            "is_seed": row.is_seed,
        }
        for row in rows
    ]


@app.get("/api/reference/rules")
def show_rules() -> dict:
    """The live policy file, so the UI shows what the engine actually applies."""
    return get_rules().raw


@app.get("/api/reference/reason-codes")
def list_reason_codes() -> list[dict]:
    return [
        {
            "code": rc.code, "severity": rc.severity.name, "title": rc.title,
            "template": rc.template, "next_action": rc.next_action,
        }
        for rc in all_codes()
    ]


@app.get("/api/reference/llm-usage")
def llm_usage(session: Session = Depends(get_session)) -> dict:
    """Calls and tokens used today - check before recording a demo."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    calls = session.exec(select(LLMCall).where(LLMCall.created_at >= since)).all()
    settings = get_settings()
    return {
        "window_hours": 24,
        "calls": len(calls),
        "failed_calls": sum(1 for c in calls if not c.ok),
        "total_tokens": sum(c.total_tokens for c in calls),
        "model": settings.gemini_model,
        "fallback_model": settings.gemini_fallback_model,
        "llm_configured": settings.llm_configured,
    }


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
@app.get("/api/samples")
def list_samples() -> list[dict]:
    """The sample gallery: one-click inputs for the demo."""
    import json

    manifest = SAMPLES_DIR / "manifest.json"
    if not manifest.exists():
        return []
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    return [
        {
            "key": row["file"].removesuffix(".pdf"),
            "file": row["file"],
            "vendor": row["vendor"],
            "invoice_number": row["number"],
            "po": row["po"],
            "total_display": row["total"],
            "kind": row["kind"],
            "scenario": row["scenario"],
            "expected_outcome": row["expected_outcome"],
        }
        for row in rows
    ]


@app.get("/api/samples/{key}/file")
def sample_file(key: str) -> FileResponse:
    path = (SAMPLES_DIR / f"{key}.pdf").resolve()
    # Keep path traversal out of a public endpoint.
    if not path.is_file() or SAMPLES_DIR.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="sample not found")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


# ---------------------------------------------------------------------------
# Demo data reset
# ---------------------------------------------------------------------------
@app.post("/api/admin/reset")
def reset_demo_data(session: Session = Depends(get_session)) -> dict:
    """Wipe runs and restore reference data to its seeded state."""
    counts = seed_reference_data(session, force=True)
    log.info("demo data reset: %s", counts)
    return {"status": "reset", "counts": counts}


# ---------------------------------------------------------------------------
# Frontend (mounted last so it cannot shadow the API)
# ---------------------------------------------------------------------------
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"),
              name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        """Serve the built single-page app.

        Client-side routes such as /dashboard do not exist as files, so any
        path that is not an API route or a real asset returns index.html and
        lets the router in the browser take over. Without this, refreshing on
        any page but the root would 404.
        """
        if full_path.startswith(("api/", "health", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not found")

        candidate = (FRONTEND_DIST / full_path).resolve()
        if (full_path and candidate.is_file()
                and FRONTEND_DIST.resolve() in candidate.parents):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def placeholder() -> dict:
        """Honest status before the UI is built - not a dead link."""
        return {
            "service": "AI Invoice Processing Pipeline",
            "status": "API is running; the user interface is not built yet",
            "try": ["/health", "/docs", "/api/samples",
                    "/api/reference/vendors", "/api/reference/purchase-orders"],
        }
