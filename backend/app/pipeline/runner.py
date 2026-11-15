"""The pipeline orchestrator - stages 1 to 10 of one run.

Responsibilities, in order of importance:

  1. Produce a decision for a real PDF.
  2. Persist every stage as it happens, so a run can be replayed exactly.
  3. Stream those stages live.
  4. Never let a failure in one run corrupt the database or affect another.

The decision logic itself lives in backend/app/rules and is not duplicated
here: this module times it, records it and reports it.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlmodel import Session, select

from ..config import get_rules, get_settings
from ..db import get_engine
from ..domain import LedgerEntry, POLine, PurchaseOrder, Vendor
from ..models import (LedgerRow, LLMCall, POLineRow, PurchaseOrderRow, Run,
                      RunStep, StoredFile, VendorRow)
from ..money import format_inr
from ..rules.codes import Finding, Severity
from ..rules.evaluate import ReferenceData, evaluate
from ..rules.validation import normalise_invoice_number
from ..serialise import json_safe
from ..services.document import IngestError, analyse
from ..services.extractor import Extractor, ExtractionError, build_extractor
from .events import bus

log = logging.getLogger(__name__)

#: The ten stages, in order, as the UI lists them.
STAGES: list[tuple[str, str]] = [
    ("ingest", "Ingest"),
    ("document_analysis", "Document analysis"),
    ("extraction", "Extraction (AI)"),
    ("validation", "Validation & normalisation"),
    ("vendor", "Vendor verification"),
    ("duplicates", "Duplicate detection"),
    ("po_matching", "PO matching"),
    ("reconciliation", "Reconciliation"),
    ("decision", "Decision"),
    ("output", "Output"),
]
STAGE_ORDER = {stage: index for index, (stage, _) in enumerate(STAGES)}
STAGE_TITLES = dict(STAGES)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


class RunRecorder:
    """Persists and streams stage transitions for one run."""

    def __init__(self, session: Session, run_id: str) -> None:
        self.session = session
        self.run_id = run_id
        self._started: dict[str, datetime] = {}

    def seed_steps(self) -> None:
        """Write every stage as pending up front, so the UI shows the shape
        of the whole process immediately rather than growing a list."""
        for stage, title in STAGES:
            self.session.add(RunStep(
                run_id=self.run_id, seq=STAGE_ORDER[stage], stage=stage,
                title=title, status="pending"))
        self.session.commit()
        bus.publish(self.run_id, {
            "type": "run.started", "run_id": self.run_id,
            "stages": [{"stage": s, "title": t, "status": "pending"}
                       for s, t in STAGES],
        })

    def start(self, stage: str) -> None:
        started = _now()
        self._started[stage] = started
        self._update(stage, status="running", started_at=started)
        bus.publish(self.run_id, {
            "type": "stage.started", "run_id": self.run_id, "stage": stage,
            "title": STAGE_TITLES[stage], "status": "running",
            "started_at": started.isoformat(),
        })

    def finish(self, stage: str, status: str, summary: str,
               detail: dict[str, Any] | None = None) -> None:
        ended = _now()
        started = self._started.get(stage, ended)
        duration = _ms(started, ended)
        self._update(stage, status=status, ended_at=ended, duration_ms=duration,
                     summary=summary, detail=detail or {})
        bus.publish(self.run_id, {
            "type": "stage.finished", "run_id": self.run_id, "stage": stage,
            "title": STAGE_TITLES[stage], "status": status, "summary": summary,
            "duration_ms": duration, "detail": json_safe(detail or {}),
        })

    def skip(self, stage: str, reason: str) -> None:
        self._update(stage, status="skipped", summary=reason,
                     detail={"skipped": True})
        bus.publish(self.run_id, {
            "type": "stage.finished", "run_id": self.run_id, "stage": stage,
            "title": STAGE_TITLES[stage], "status": "skipped",
            "summary": reason, "duration_ms": 0, "detail": {"skipped": True},
        })

    def _update(self, stage: str, **fields: Any) -> None:
        step = self.session.exec(
            select(RunStep).where(RunStep.run_id == self.run_id,
                                  RunStep.stage == stage)).first()
        if step is None:  # pragma: no cover - seed_steps always runs first
            step = RunStep(run_id=self.run_id, seq=STAGE_ORDER[stage],
                           stage=stage, title=STAGE_TITLES[stage])
            self.session.add(step)
        for key, value in fields.items():
            setattr(step, key, json_safe(value) if key == "detail" else value)
        self.session.add(step)
        self.session.commit()


def _source_label(document) -> str:
    if document.kind == "image":
        return "uploaded image"
    return "page images" if document.is_scanned else "text layer"


def _document_summary(document) -> str:
    """Why this document is taking the path it is taking, in one line.

    An image never had a text layer to check, so saying "no text layer found"
    about one would imply a test that was never run.
    """
    if document.image:
        image = document.image
        sent = (f"downscaled to {image['processed_width']}"
                f"×{image['processed_height']} px"
                if image["downscaled"] else "sent at full size")
        rotated = (", EXIF rotation applied"
                   if image["exif_rotation_applied"] else "")
        return (f"Image upload ({image['format']}, {image['original_width']}"
                f"×{image['original_height']}) → vision extraction "
                f"({sent}{rotated}).")
    if document.is_scanned:
        return (f"No text layer ({document.chars_per_page:.0f} chars/page) - "
                f"reading {document.page_count} page image(s) with the vision "
                f"model.")
    return (f"Text layer found ({document.chars_per_page:.0f} chars/page) - "
            f"sending text, no OCR needed.")


def _status_for(findings: list[Finding]) -> str:
    """A stage is green unless something it found says otherwise."""
    if not findings:
        return "passed"
    worst = max(f.severity for f in findings)
    if worst >= Severity.REJECT:
        return "failed"
    if worst == Severity.REVIEW:
        return "warning"
    return "passed"


def _summarise(stage: str, findings: list[Finding], detail: dict[str, Any]) -> str:
    """One line a non-technical reader understands, per stage."""
    blocking = [f for f in findings if f.severity >= Severity.REVIEW]
    if blocking:
        return blocking[0].message

    if stage == "validation":
        total = detail.get("total", "-")
        lines = detail.get("line_count", 0)
        return f"Read {lines} line item(s), total INR {total}. All checks tied out."
    if stage == "vendor":
        if detail.get("matched"):
            method = ("GSTIN" if detail.get("method") == "gstin"
                      else f"name match at {detail.get('score', 0):.0%}")
            return f"Matched {detail.get('legal_name')} by {method}."
        return "No vendor on the master matched this supplier."
    if stage == "duplicates":
        return (f"No duplicate found across "
                f"{detail.get('ledger_entries_searched', 0)} processed invoices.")
    if stage == "po_matching":
        if detail.get("po_number"):
            how = ("quoted on the invoice" if detail.get("method") == "explicit"
                   else f"inferred at {detail.get('score', 0):.0%} confidence")
            return f"{detail['po_number']} ({how})."
        return "No purchase order matched."
    if stage == "reconciliation":
        if detail.get("expected_amount") in (None, "-"):
            return "No PO to reconcile against."
        difference = detail.get("difference", "-")
        return (f"Invoice {detail.get('actual_amount')} vs expected "
                f"{detail.get('expected_amount')} (difference {difference}).")
    if stage == "decision":
        return f"{detail.get('outcome')} from {detail.get('rules_evaluated', 0)} checks."

    notes = [f.message for f in findings]
    return notes[0] if notes else "Completed."


# ---------------------------------------------------------------------------
# Reference data from the database
# ---------------------------------------------------------------------------
def load_reference(session: Session) -> ReferenceData:
    vendors = [
        Vendor(
            vendor_id=row.vendor_id, legal_name=row.legal_name,
            aliases=[a for a in row.aliases.split("|") if a], gstin=row.gstin,
            status=row.status, payment_terms=row.payment_terms,
            bank_account_last4=row.bank_account_last4,
        )
        for row in session.exec(select(VendorRow)).all()
    ]

    lines: dict[str, list[POLine]] = {}
    for row in session.exec(select(POLineRow)).all():
        lines.setdefault(row.po_number, []).append(POLine(
            po_number=row.po_number, line_no=row.line_no, sku=row.sku,
            description=row.description, qty=row.qty,
            unit_price=row.unit_price, line_amount=row.line_amount))

    purchase_orders = [
        PurchaseOrder(
            po_number=row.po_number, vendor_id=row.vendor_id,
            issue_date=row.issue_date, status=row.status, currency=row.currency,
            total_amount=row.total_amount,
            amount_invoiced_to_date=row.amount_invoiced_to_date,
            lines=sorted(lines.get(row.po_number, []), key=lambda l: l.line_no),
            note=row.note,
        )
        for row in session.exec(select(PurchaseOrderRow)).all()
    ]

    ledger = [
        LedgerEntry(
            invoice_id=row.invoice_id, vendor_id=row.vendor_id,
            invoice_number=row.invoice_number, invoice_date=row.invoice_date,
            currency=row.currency, total_amount=row.total_amount,
            po_number=row.po_number, outcome=row.outcome,
            processed_at=row.processed_at, file_sha256=row.file_sha256,
        )
        for row in session.exec(select(LedgerRow)).all()
    ]
    return ReferenceData(vendors, purchase_orders, ledger)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def execute_run(run_id: str, data: bytes, filename: str,
                sample_key: str | None = None,
                extractor: Extractor | None = None) -> str:
    """Run the whole pipeline for one PDF. Returns the final outcome.

    Called in a worker thread. Owns its own database session so it never
    shares one across threads.
    """
    rules = get_rules()
    started = _now()

    with Session(get_engine()) as session:
        run = session.get(Run, run_id)
        if run is None:
            run = Run(id=run_id, filename=filename, sample_key=sample_key)
            session.add(run)
            session.commit()

        recorder = RunRecorder(session, run_id)
        recorder.seed_steps()

        try:
            outcome = _execute(session, recorder, run, data, filename, rules,
                               extractor)
        except Exception as exc:  # pragma: no cover - last line of defence
            log.exception("run %s crashed", run_id)
            # The flush that failed leaves the session unusable; without this
            # rollback the failure handler fails too and the run is stranded
            # at "running" forever.
            session.rollback()
            run = session.get(Run, run_id)
            outcome = _fail(session, recorder, run, "output",
                            "LLM_UNAVAILABLE",
                            {"attempts": 0, "detail": f"internal error: {exc}"},
                            traceback.format_exc())

        run = session.get(Run, run_id)
        run.finished_at = _now()
        run.duration_ms = _ms(started, run.finished_at)
        session.add(run)
        session.commit()

        bus.publish(run_id, {
            "type": "run.finished", "run_id": run_id, "outcome": outcome,
            "status": run.status, "duration_ms": run.duration_ms,
        })
        return outcome


def _execute(session: Session, recorder: RunRecorder, run: Run, data: bytes,
             filename: str, rules, extractor: Extractor | None) -> str:
    # --- 1. Ingest ---------------------------------------------------------
    recorder.start("ingest")
    try:
        document, analysis_findings = analyse(data, rules, filename)
    except IngestError as exc:
        recorder.finish("ingest", "failed", Finding(exc.code, "ingest",
                                                    exc.evidence).message,
                        {"filename": filename, "size_bytes": len(data)})
        return _fail(session, recorder, run, "document_analysis", exc.code,
                     exc.evidence)

    run.file_sha256 = document.sha256
    run.size_bytes = document.size_bytes
    run.page_count = document.page_count
    run.is_scanned = document.is_scanned
    run.file_kind = document.kind
    session.add(run)
    session.add(StoredFile(run_id=run.id, filename=filename,
                           content_type=document.media_type,
                           size_bytes=len(data), content=data))
    session.commit()

    recorder.finish("ingest", "passed",
                    f"Accepted {filename} as {document.kind_label} "
                    f"({document.size_bytes / 1024:.0f} KB, "
                    + (f"{document.image['original_width']}"
                       f"×{document.image['original_height']} px)."
                       if document.image else
                       f"{document.page_count} page(s))."),
                    {"filename": filename, "sha256": document.sha256,
                     "size_bytes": document.size_bytes,
                     "page_count": document.page_count,
                     "file_kind": document.kind,
                     "file_kind_label": document.kind_label,
                     "media_type": document.media_type,
                     "max_file_mb": rules.get("ingest.max_file_mb"),
                     "accepted_formats": rules.strings("ingest.accepted_formats")})

    # --- 2. Document analysis ----------------------------------------------
    recorder.start("document_analysis")
    recorder.finish(
        "document_analysis", _status_for(analysis_findings),
        _document_summary(document),
        {"is_scanned": document.is_scanned,
         "file_kind": document.kind,
         "file_kind_label": document.kind_label,
         "chars_per_page": document.chars_per_page,
         "threshold": rules.integer("extraction.scanned_page_char_threshold"),
         "path": document.path_label,
         "image": document.image,
         "text_preview": document.text[:1500]})

    # --- 3. Extraction ------------------------------------------------------
    recorder.start("extraction")
    try:
        extractor = extractor or build_extractor()
        result = extractor.extract(document)
    except ExtractionError as exc:
        _record_llm_failure(session, run.id, exc)
        recorder.finish("extraction", "failed",
                        Finding(exc.code, "extraction", exc.evidence).message,
                        {"error_code": exc.code, **exc.evidence})
        return _fail(session, recorder, run, "validation", exc.code, exc.evidence)

    # A replayed extraction made no call, so it must not appear in the quota
    # counter that is checked before a demo.
    if not result.replayed:
        session.add(LLMCall(
            run_id=run.id, model=result.model, attempts=result.attempts,
            prompt_tokens=result.prompt_tokens,
            response_tokens=result.response_tokens,
            total_tokens=result.total_tokens,
            duration_ms=result.duration_ms, ok=True))
    run.llm_model = result.model
    run.llm_tokens = result.total_tokens
    run.llm_calls = result.attempts
    run.extraction = json_safe(result.redacted_log())
    session.add(run)
    session.commit()

    recorder.finish(
        "extraction", "warning" if result.warnings else "passed",
        (f"Replayed the extraction recorded by the earlier run "
         f"({len(result.invoice.lines)} line item(s), model {result.model}) - "
         f"no new AI call was made."
         if result.replayed else
         f"Read {len(result.invoice.lines)} line item(s) from the "
         f"{_source_label(document)} using "
         f"{result.model} in {result.duration_ms / 1000:.1f}s."),
        result.redacted_log())

    # --- 4-10. The deterministic stages -------------------------------------
    prior = list(analysis_findings)

    def on_stage(stage: str, findings: list[Finding], detail: dict[str, Any]) -> None:
        if detail.get("skipped"):
            recorder.skip(stage, detail.get("reason", "Not applicable."))
            return
        recorder.start(stage)
        recorder.finish(stage, _status_for(findings),
                        _summarise(stage, findings, detail), detail)

    reference = load_reference(session)
    evaluation = evaluate(result.invoice, reference, document.sha256, rules,
                          prior_findings=prior, on_stage=on_stage)

    # --- 10. Output ----------------------------------------------------------
    recorder.start("output")
    decision = evaluation.decision
    effect = _apply_side_effects(session, run, evaluation)

    invoice = evaluation.invoice
    run.status = "completed"
    run.outcome = decision.outcome
    run.top_reason = decision.top_reason
    run.explanation = decision.explanation
    run.next_action = decision.next_action
    run.draft_email = json_safe(decision.draft_email)
    run.findings = json_safe([f.to_dict() for f in decision.findings])
    run.reconciliation = json_safe(_recon_payload(evaluation))
    run.po_candidates = json_safe(_candidates_payload(evaluation))
    run.ledger_effect = json_safe(effect)
    if evaluation.vendor:
        run.vendor_id = evaluation.vendor.vendor_id
        run.vendor_name = evaluation.vendor.legal_name
    if invoice is not None:
        run.invoice_number = invoice.invoice_number
        run.invoice_date = invoice.invoice_date
        run.total_amount = invoice.total
        run.currency = invoice.currency
        run.confidence = json_safe(invoice.confidence)
    if evaluation.matched_po:
        run.po_number = evaluation.matched_po.po_number
    session.add(run)
    session.commit()

    recorder.finish("output", "passed",
                    f"{decision.outcome}. {decision.next_action}",
                    {"outcome": decision.outcome,
                     "explanation": decision.explanation,
                     "next_action": decision.next_action,
                     "draft_email": decision.draft_email,
                     "ledger_effect": effect})
    return decision.outcome


def _recon_payload(evaluation) -> dict[str, Any]:
    from ..rules.evaluate import _reconciliation_detail
    if evaluation.reconciliation is None:
        return {}
    return _reconciliation_detail(evaluation.reconciliation)


def _candidates_payload(evaluation) -> list[dict[str, Any]]:
    if evaluation.po_match is None:
        return []
    from ..rules.evaluate import _po_detail
    return _po_detail(evaluation.po_match).get("candidates", [])


def _apply_side_effects(session: Session, run: Run, evaluation) -> dict | None:
    """Write the ledger entry, and move the PO balance on approval.

    Every processed invoice is recorded - approvals, holds and rejections
    alike - because duplicate detection must see held invoices too. Only an
    approval moves money against the PO.
    """
    invoice = evaluation.invoice
    if invoice is None:
        return None

    session.add(LedgerRow(
        invoice_id=f"R-{run.id[:12]}",
        vendor_id=evaluation.vendor.vendor_id if evaluation.vendor else "",
        invoice_number=invoice.invoice_number or "",
        invoice_number_normalised=normalise_invoice_number(invoice.invoice_number) or "",
        invoice_date=invoice.invoice_date,
        currency=invoice.currency,
        total_amount=invoice.total or Decimal("0.00"),
        po_number=evaluation.matched_po.po_number if evaluation.matched_po else None,
        outcome=evaluation.outcome,
        processed_at=_now().isoformat(),
        file_sha256=run.file_sha256,
        run_id=run.id,
        is_seed=False,
    ))

    effect = None
    if evaluation.outcome == "APPROVED" and evaluation.matched_po and invoice.total:
        po_row = session.get(PurchaseOrderRow, evaluation.matched_po.po_number)
        if po_row is not None:
            before = po_row.amount_invoiced_to_date
            after = (before + invoice.total).quantize(Decimal("0.01"))
            po_row.amount_invoiced_to_date = after
            if after >= po_row.total_amount:
                po_row.status = "closed"
            session.add(po_row)
            effect = {
                "po_number": po_row.po_number,
                "invoiced_before": format_inr(before),
                "invoiced_after": format_inr(after),
                "remaining_after": format_inr(po_row.total_amount - after),
                "amount_applied": format_inr(invoice.total),
                "po_closed": po_row.status == "closed",
            }
    session.commit()
    return effect


def _fail(session: Session, recorder: RunRecorder, run: Run, from_stage: str,
          code: str, evidence: dict[str, Any],
          detail: str | None = None) -> str:
    """Mark the run FAILED, skip the stages that never got to run, and say why."""
    finding = Finding(code, "pipeline", evidence)

    start = STAGE_ORDER.get(from_stage, 0)
    for stage, _ in STAGES[start:]:
        if stage == "output":
            continue
        recorder.skip(stage, "Not reached - the run failed earlier.")

    run.status = "failed"
    run.outcome = "FAILED"
    run.top_reason = code
    run.explanation = finding.message
    run.next_action = finding.next_action
    run.error_message = detail or finding.message
    run.findings = json_safe([finding.to_dict()])
    session.add(run)
    session.commit()

    recorder.finish("output", "failed", finding.message, {
        "outcome": "FAILED", "error_code": code,
        "explanation": finding.message, "next_action": finding.next_action,
    })
    return "FAILED"


def _record_llm_failure(session: Session, run_id: str, exc: ExtractionError) -> None:
    settings = get_settings()
    session.add(LLMCall(
        run_id=run_id, model=settings.gemini_model,
        attempts=int(exc.evidence.get("attempts", 0) or 0),
        ok=False, error_code=exc.code))
    session.commit()


def new_run_id() -> str:
    return uuid.uuid4().hex
