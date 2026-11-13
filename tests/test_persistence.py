"""Persistence and failure handling.

These exist because of two bugs that 234 unit tests missed and only a live
run exposed: a Decimal reaching a JSON column, and the failure handler itself
failing on the poisoned session afterwards.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from backend.app.serialise import json_safe

D = Decimal


# ---------------------------------------------------------------------------
# The sanitiser
# ---------------------------------------------------------------------------
def test_decimals_become_strings_not_floats():
    """This is an audit trail; a float's rounding has no place in it."""
    assert json_safe(D("1.10")) == "1.10"
    assert json_safe(D("50150.00")) == "50150.00"


def test_dates_become_iso_strings():
    assert json_safe(date(2026, 11, 4)) == "2026-11-04"
    assert json_safe(datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)).startswith(
        "2026-11-04T09:30")


def test_nested_structures_are_cleaned_throughout():
    payload = {
        "lines": [{"price": D("485.00"), "qty": D("120")}],
        "meta": {"when": date(2026, 11, 4), "nested": {"pct": D("2.00")}},
        "plain": ["a", 1, True, None, 2.5],
    }
    cleaned = json_safe(payload)
    json.dumps(cleaned)  # must not raise
    assert cleaned["lines"][0]["price"] == "485.00"
    assert cleaned["meta"]["nested"]["pct"] == "2.00"
    assert cleaned["plain"] == ["a", 1, True, None, 2.5]


def test_findings_with_decimal_evidence_survive_serialisation():
    """The exact shape that broke run 04a in the live sweep."""
    from backend.app.rules.codes import Finding

    finding = Finding("TOTAL_VARIANCE_WITHIN_TOLERANCE", "reconciliation", {
        "expected": "1,35,700.00", "actual": "1,35,880.00",
        "difference": "180.00", "pct": D("0.13"), "band": "500.00 cap",
    })
    json.dumps(json_safe([finding.to_dict()]))


def test_every_scenario_finding_set_is_json_serialisable(vendors, purchase_orders,
                                                         ledger):
    """Sweeps every sample, so a new Decimal in evidence cannot slip through."""
    import copy

    from backend.app.rules.evaluate import ReferenceData, evaluate

    from .test_extraction import ALL_STEMS, extracted

    for stem in ALL_STEMS:
        reference = ReferenceData(copy.deepcopy(vendors),
                                  copy.deepcopy(purchase_orders),
                                  copy.deepcopy(ledger))
        result = evaluate(extracted(stem), reference, f"hash-{stem}")
        payload = json_safe([f.to_dict() for f in result.findings])
        json.dumps(payload)           # must not raise for any sample


def test_stage_detail_payloads_are_json_serialisable(vendors, purchase_orders,
                                                     ledger):
    import copy

    from backend.app.rules.evaluate import ReferenceData, evaluate

    from .test_extraction import ALL_STEMS, extracted

    captured: list[dict] = []

    for stem in ALL_STEMS:
        reference = ReferenceData(copy.deepcopy(vendors),
                                  copy.deepcopy(purchase_orders),
                                  copy.deepcopy(ledger))
        evaluate(extracted(stem), reference, "h",
                 on_stage=lambda stage, findings, detail: captured.append(detail))

    assert captured
    for detail in captured:
        json.dumps(json_safe(detail))


# ---------------------------------------------------------------------------
# A full run against a temporary database
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from backend.app import db as db_module
    from backend.app.config import get_settings

    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    db_module.reset_engine()
    db_module.init_db()
    yield db_module
    db_module.reset_engine()
    get_settings.cache_clear()


class StubExtractor:
    """Returns a recorded extraction, so no API call is made."""

    def __init__(self, stem: str) -> None:
        self.stem = stem

    def extract(self, document):
        from backend.app.services.extractor import ExtractionResult, _parse

        from .test_extraction import load_fixture

        fixture = load_fixture(self.stem)
        invoice, warnings = _parse(fixture["response"])
        return ExtractionResult(
            invoice=invoice, model=fixture["model"], attempts=1,
            duration_ms=1234, prompt_tokens=fixture["tokens"]["prompt"],
            response_tokens=fixture["tokens"]["response"],
            total_tokens=fixture["tokens"]["total"],
            raw_response=fixture["response"],
            input_kind=fixture["input_kind"], warnings=warnings)


class ExplodingExtractor:
    def extract(self, document):
        raise RuntimeError("provider melted")


def test_a_full_run_persists_every_stage(temp_db):
    """Run 04a - the one whose Decimal evidence broke persistence."""
    from pathlib import Path

    from backend.app.models import Run, RunStep, StoredFile
    from backend.app.pipeline.runner import STAGES, execute_run, new_run_id

    data = Path("sample_invoices/04a_within_tolerance_trident.pdf").read_bytes()
    run_id = new_run_id()
    outcome = execute_run(run_id, data, "04a.pdf", "04a",
                          extractor=StubExtractor("04a_within_tolerance_trident"))

    assert outcome == "APPROVED"

    with Session(temp_db.get_engine()) as session:
        run = session.get(Run, run_id)
        assert run.status == "completed"
        assert run.outcome == "APPROVED"
        assert run.top_reason == "TOTAL_VARIANCE_WITHIN_TOLERANCE"
        assert run.duration_ms is not None

        # Everything in a JSON column must round-trip.
        json.dumps(run.findings)
        json.dumps(run.reconciliation)
        json.dumps(run.extraction)
        json.dumps(run.confidence)

        steps = session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
        assert len(steps) == len(STAGES)
        assert all(s.status in ("passed", "warning") for s in steps), \
            [(s.stage, s.status) for s in steps]
        assert all(s.summary for s in steps)

        stored = session.get(StoredFile, run_id)
        assert stored is not None and stored.content[:5] == b"%PDF-"


def test_an_approved_run_moves_the_po_balance(temp_db):
    from pathlib import Path

    from backend.app.models import PurchaseOrderRow
    from backend.app.pipeline.runner import execute_run, new_run_id

    data = Path("sample_invoices/02a_split_po_nova_1of3.pdf").read_bytes()
    execute_run(new_run_id(), data, "02a.pdf", "02a",
                extractor=StubExtractor("02a_split_po_nova_1of3"))

    with Session(temp_db.get_engine()) as session:
        po = session.get(PurchaseOrderRow, "PO-4510")
        assert po.amount_invoiced_to_date == D("200600.00")


def test_a_held_run_does_not_move_the_po_balance(temp_db):
    from pathlib import Path

    from backend.app.models import PurchaseOrderRow
    from backend.app.pipeline.runner import execute_run, new_run_id

    data = Path("sample_invoices/04b_price_creep_kaveri.pdf").read_bytes()
    outcome = execute_run(new_run_id(), data, "04b.pdf", "04b",
                          extractor=StubExtractor("04b_price_creep_kaveri"))

    assert outcome == "NEEDS_REVIEW"
    with Session(temp_db.get_engine()) as session:
        po = session.get(PurchaseOrderRow, "PO-4531")
        assert po.amount_invoiced_to_date == D("0.00")


def test_a_non_pdf_upload_fails_visibly(temp_db):
    from backend.app.models import Run, RunStep
    from backend.app.pipeline.runner import execute_run, new_run_id

    run_id = new_run_id()
    outcome = execute_run(run_id, b"\x89PNG\r\n\x1a\n" + b"0" * 500, "scan.png")

    assert outcome == "FAILED"
    with Session(temp_db.get_engine()) as session:
        run = session.get(Run, run_id)
        assert run.status == "failed"
        assert run.top_reason == "FILE_NOT_PDF"
        assert "PNG" in run.explanation
        assert run.next_action                      # always actionable
        steps = session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
        assert {s.stage for s in steps if s.status == "failed"} >= {"ingest"}


def test_an_extraction_failure_marks_the_run_failed_not_stranded(temp_db):
    """The second bug: the failure handler must itself survive."""
    from pathlib import Path

    from backend.app.models import Run
    from backend.app.pipeline.runner import execute_run, new_run_id

    data = Path("sample_invoices/00_happy_path_bharat_steel.pdf").read_bytes()
    run_id = new_run_id()
    outcome = execute_run(run_id, data, "00.pdf", extractor=ExplodingExtractor())

    assert outcome == "FAILED"
    with Session(temp_db.get_engine()) as session:
        run = session.get(Run, run_id)
        assert run.status == "failed", "a crashed run must never stay 'running'"
        assert run.outcome == "FAILED"
        assert run.explanation
        assert run.finished_at is not None


def test_a_failed_run_does_not_poison_the_next_one(temp_db):
    """A failure in one run must never affect another."""
    from pathlib import Path

    from backend.app.models import Run
    from backend.app.pipeline.runner import execute_run, new_run_id

    execute_run(new_run_id(), b"not a pdf at all", "junk.txt")

    data = Path("sample_invoices/00_happy_path_bharat_steel.pdf").read_bytes()
    good_id = new_run_id()
    outcome = execute_run(good_id, data, "00.pdf", "00",
                          extractor=StubExtractor("00_happy_path_bharat_steel"))

    assert outcome == "APPROVED"
    with Session(temp_db.get_engine()) as session:
        assert session.get(Run, good_id).status == "completed"
