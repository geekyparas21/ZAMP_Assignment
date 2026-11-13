"""Run endpoints: start a run, watch it live, and read past ones.

The live view and the dashboard's replay of an old run are deliberately the
same shape of data. A browser that connects late gets the stored steps first
and then the live tail, so there is no race between starting a run and
subscribing to it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     HTTPException, Request, UploadFile)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlmodel import Session, func, select

from .config import SAMPLES_DIR
from .db import get_engine, get_session
from .models import LedgerRow, Run, RunStep, StoredFile
from .money import format_inr
from .pipeline.events import bus, sse_format
from .pipeline.runner import STAGES, execute_run, new_run_id
from .services.extractor import RecordedExtractor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["runs"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def run_summary(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_ms": run.duration_ms,
        "filename": run.filename,
        "sample_key": run.sample_key,
        "status": run.status,
        "outcome": run.outcome,
        "top_reason": run.top_reason,
        "vendor_id": run.vendor_id,
        "vendor_name": run.vendor_name,
        "invoice_number": run.invoice_number,
        "invoice_date": run.invoice_date.isoformat() if run.invoice_date else None,
        "total_amount": str(run.total_amount) if run.total_amount is not None else None,
        "total_display": format_inr(run.total_amount),
        "currency": run.currency,
        "po_number": run.po_number,
        "is_scanned": run.is_scanned,
        "page_count": run.page_count,
        "llm_model": run.llm_model,
        "llm_tokens": run.llm_tokens,
    }


def step_payload(step: RunStep) -> dict[str, Any]:
    return {
        "stage": step.stage, "title": step.title, "status": step.status,
        "seq": step.seq, "duration_ms": step.duration_ms,
        "summary": step.summary, "detail": step.detail or {},
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "ended_at": step.ended_at.isoformat() if step.ended_at else None,
    }


def run_detail(run: Run, steps: list[RunStep]) -> dict[str, Any]:
    payload = run_summary(run)
    payload.update({
        "explanation": run.explanation,
        "next_action": run.next_action,
        "error_message": run.error_message,
        "findings": run.findings or [],
        "extraction": run.extraction or {},
        "reconciliation": run.reconciliation or {},
        "po_candidates": run.po_candidates or [],
        "confidence": run.confidence or {},
        "draft_email": run.draft_email,
        "ledger_effect": run.ledger_effect,
        "file_sha256": run.file_sha256,
        "steps": [step_payload(s) for s in sorted(steps, key=lambda s: s.seq)],
    })
    return payload


# ---------------------------------------------------------------------------
# Starting a run
# ---------------------------------------------------------------------------
def _start(background: BackgroundTasks, session: Session, data: bytes,
           filename: str, sample_key: str | None) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    run_id = new_run_id()
    session.add(Run(id=run_id, filename=filename, sample_key=sample_key,
                    status="running"))
    session.commit()

    # Run off the event loop: the pipeline is blocking work (PDF parsing, an
    # HTTP call, rules) and would otherwise stall every other request.
    background.add_task(_run_in_thread, run_id, data, filename, sample_key)
    return {"run_id": run_id, "status": "running",
            "stages": [{"stage": s, "title": t} for s, t in STAGES]}


async def _run_in_thread(run_id: str, data: bytes, filename: str,
                         sample_key: str | None) -> None:
    try:
        await asyncio.to_thread(execute_run, run_id, data, filename, sample_key)
    except Exception:  # pragma: no cover - execute_run handles its own errors
        log.exception("run %s failed outside the pipeline", run_id)


@router.post("/runs")
async def start_run(background: BackgroundTasks, file: UploadFile = File(...),
                    session: Session = Depends(get_session)) -> dict[str, Any]:
    """Upload a PDF and start processing it."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return _start(background, session, data, file.filename or "upload.pdf", None)


@router.post("/runs/sample")
def start_sample_run(background: BackgroundTasks, key: str = Form(...),
                     session: Session = Depends(get_session)) -> dict[str, Any]:
    """Start a run from one of the bundled sample invoices.

    The sample is read and handed to the pipeline as ordinary bytes - no code
    path anywhere branches on which sample it is.
    """
    path = (SAMPLES_DIR / f"{key}.pdf").resolve()
    if not path.is_file() or SAMPLES_DIR.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="Sample not found.")
    return _start(background, session, path.read_bytes(), path.name, key)


@router.post("/runs/{run_id}/rerun")
def rerun(run_id: str, background: BackgroundTasks,
          session: Session = Depends(get_session)) -> dict[str, Any]:
    """Decide the same document again against the reference data as it is now.

    This is the other half of being able to add a vendor or a PO on screen:
    without it, "we have onboarded them" cannot be demonstrated against the
    invoice that prompted it.

    Two things make it honest rather than a reset button:

      * the stored model response is replayed, so the only thing that can have
        changed is the reference data, which is the whole point;
      * the earlier run's ledger entry is withdrawn first. It recorded an
        assessment that this run supersedes, and leaving it in place would make
        the document its own duplicate.
    """
    source = session.get(Run, run_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    stored = session.get(StoredFile, run_id)
    if stored is None or not stored.content:
        raise HTTPException(
            status_code=409,
            detail="The original file for this run is no longer stored, so it "
                   "cannot be re-run.")

    # Withdraw the superseded assessment, so duplicate detection does not see
    # this document as a prior submission of itself.
    for row in session.exec(
            select(LedgerRow).where(LedgerRow.run_id == run_id)).all():
        session.delete(row)

    new_id = new_run_id()
    session.add(Run(id=new_id, filename=source.filename,
                    sample_key=source.sample_key, status="running"))
    session.commit()

    background.add_task(_rerun_in_thread, new_id, bytes(stored.content),
                        source.filename, source.sample_key,
                        dict(source.extraction or {}))
    return {"run_id": new_id, "status": "running", "rerun_of": run_id,
            "stages": [{"stage": s, "title": t} for s, t in STAGES]}


async def _rerun_in_thread(run_id: str, data: bytes, filename: str,
                           sample_key: str | None,
                           stored_extraction: dict[str, Any]) -> None:
    recorded = RecordedExtractor(stored_extraction)
    # A run that never got as far as extraction has nothing to replay, so it
    # is re-read for real rather than silently failing.
    extractor = recorded if recorded.usable else None
    try:
        await asyncio.to_thread(execute_run, run_id, data, filename, sample_key,
                                extractor)
    except Exception:  # pragma: no cover - execute_run handles its own errors
        log.exception("re-run %s failed outside the pipeline", run_id)


# ---------------------------------------------------------------------------
# Live stream
# ---------------------------------------------------------------------------
@router.get("/runs/{run_id}/events")
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events for one run.

    Stored steps are replayed first, so a client that subscribes after the run
    started - or after it finished - still sees the full picture.
    """
    queue = bus.subscribe(run_id)

    async def generator():
        try:
            # Read the snapshot into plain values before the session closes;
            # using ORM objects afterwards would hit a detached instance.
            with Session(get_engine()) as session:
                run = session.get(Run, run_id)
                if run is None:
                    yield sse_format({"type": "error", "run_id": run_id,
                                      "message": "Run not found."})
                    return
                steps = session.exec(
                    select(RunStep).where(RunStep.run_id == run_id)).all()
                snapshot = {
                    "type": "run.snapshot", "run_id": run_id,
                    "status": run.status, "outcome": run.outcome,
                    "steps": [step_payload(s)
                              for s in sorted(steps, key=lambda s: s.seq)],
                }
                status, outcome = run.status, run.outcome

            yield sse_format(snapshot)

            if status != "running":
                yield sse_format({"type": "run.finished", "run_id": run_id,
                                  "outcome": outcome, "status": status})
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    # Keep-alive: proxies drop idle connections, and a free
                    # host's first run can be slow.
                    yield ": keep-alive\n\n"
                    continue
                yield sse_format(event)
                if event.get("type") == "run.finished":
                    break
        finally:
            bus.unsubscribe(run_id, queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# ---------------------------------------------------------------------------
# Reading runs
# ---------------------------------------------------------------------------
@router.get("/runs")
def list_runs(limit: int = 100, outcome: str | None = None,
              search: str | None = None,
              session: Session = Depends(get_session)) -> list[dict]:
    statement = select(Run).order_by(Run.created_at.desc()).limit(min(limit, 500))
    if outcome:
        statement = statement.where(Run.outcome == outcome.upper())
    runs = session.exec(statement).all()

    if search:
        needle = search.lower()
        runs = [
            r for r in runs
            if needle in (r.vendor_name or "").lower()
            or needle in (r.invoice_number or "").lower()
            or needle in (r.po_number or "").lower()
            or needle in (r.filename or "").lower()
        ]
    return [run_summary(r) for r in runs]


@router.get("/runs/stats")
def run_stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    """KPI tiles for the dashboard."""
    runs = session.exec(select(Run)).all()
    completed = [r for r in runs if r.outcome]
    by_outcome = {name: sum(1 for r in completed if r.outcome == name)
                  for name in ("APPROVED", "NEEDS_REVIEW", "REJECTED", "FAILED")}
    durations = [r.duration_ms for r in runs if r.duration_ms]
    value = sum((r.total_amount for r in completed
                 if r.outcome == "APPROVED" and r.total_amount), 0)
    total = len(completed) or 1

    return {
        "total_runs": len(runs),
        "completed": len(completed),
        "by_outcome": by_outcome,
        "auto_approved_pct": round(by_outcome["APPROVED"] / total * 100, 1),
        "needs_review_pct": round(by_outcome["NEEDS_REVIEW"] / total * 100, 1),
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
        "total_value_approved": str(value),
        "total_value_display": format_inr(value),
        "total_tokens": sum(r.llm_tokens for r in runs),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    steps = session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
    return run_detail(run, steps)


@router.get("/runs/{run_id}/file")
def get_run_file(run_id: str, session: Session = Depends(get_session)) -> Response:
    """The original PDF, served from the database rather than disk."""
    stored = session.get(StoredFile, run_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="File not found for this run.")
    return Response(
        content=stored.content, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'inline; filename="{stored.filename or "invoice.pdf"}"'})


@router.get("/runs/{run_id}/audit")
def get_audit_json(run_id: str, session: Session = Depends(get_session)) -> Response:
    """The complete audit trail for one run, as a download."""
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    steps = session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": run_detail(run, steps),
        "policy_version": _policy_version(),
    }
    body = json.dumps(payload, indent=2, default=str)
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="audit-{run_id[:12]}.json"'})


def _policy_version() -> str:
    from .config import get_rules
    try:
        return str(get_rules().get("meta.policy_version"))
    except Exception:
        return "unknown"


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    for step in session.exec(select(RunStep).where(RunStep.run_id == run_id)).all():
        session.delete(step)
    stored = session.get(StoredFile, run_id)
    if stored:
        session.delete(stored)
    for row in session.exec(
            select(LedgerRow).where(LedgerRow.run_id == run_id)).all():
        session.delete(row)
    session.delete(run)
    session.commit()
    return {"status": "deleted", "run_id": run_id}
