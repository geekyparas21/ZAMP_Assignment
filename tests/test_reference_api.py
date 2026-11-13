"""The reference-data writes and the Re-run endpoint.

These drive the HTTP surface against a temporary SQLite file. No API call is
made: the one run that happens here is fed a recorded extraction, and the
re-run replays what that run stored.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

D = Decimal


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.app import db as db_module
    from backend.app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    get_settings.cache_clear()
    db_module.reset_engine()
    db_module.init_db()

    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client

    db_module.reset_engine()
    get_settings.cache_clear()


VALID_VENDOR = {
    "legal_name": "Meridian Industrial Supplies Private Limited",
    "gstin": "27AAMCM1234N1Z5",
    "aliases": ["Meridian Industrial Supplies", "Meridian Supplies"],
    "status": "approved",
    "payment_terms": "Net 30",
    "bank_account_last4": "6690",
}


# ===========================================================================
# Adding a vendor
# ===========================================================================
def test_a_vendor_can_be_onboarded_and_is_then_on_the_master(client):
    response = client.post("/api/reference/vendors", json=VALID_VENDOR)
    assert response.status_code == 201, response.text

    added = response.json()
    assert added["vendor_id"] == "V011"      # next in the seeded series
    assert added["status"] == "approved"

    listed = client.get("/api/reference/vendors").json()
    row = next(v for v in listed if v["vendor_id"] == "V011")
    assert row["gstin"] == "27AAMCM1234N1Z5"
    assert row["aliases"] == ["Meridian Industrial Supplies", "Meridian Supplies"]


def test_an_explicit_vendor_id_is_honoured(client):
    response = client.post("/api/reference/vendors",
                           json={**VALID_VENDOR, "vendor_id": "V900"})
    assert response.status_code == 201
    assert response.json()["vendor_id"] == "V900"


@pytest.mark.parametrize("gstin,fragment", [
    ("27AAMCM1234N1Z4", "check digit"),
    ("27AAMCM1234N1Z", "15 characters"),
    ("NOTAGSTINATALL1", "pattern"),
])
def test_a_malformed_gstin_is_refused_with_the_reason(client, gstin, fragment):
    """The same checksum the pipeline applies, applied where the typo is made."""
    response = client.post("/api/reference/vendors",
                           json={**VALID_VENDOR, "gstin": gstin})
    assert response.status_code == 422
    assert fragment in response.text


def test_a_duplicate_gstin_is_refused(client):
    """Two vendors on one GSTIN would make vendor matching row-order dependent."""
    response = client.post("/api/reference/vendors", json={
        **VALID_VENDOR, "gstin": "27AABCB1234D1ZC"})   # already V001's
    assert response.status_code == 409
    assert "Bharat Steel" in response.text


def test_a_bad_status_is_refused(client):
    response = client.post("/api/reference/vendors",
                           json={**VALID_VENDOR, "status": "probationary"})
    assert response.status_code == 422
    assert "approved, pending or blocked" in response.text


def test_a_whole_account_number_is_refused_not_silently_truncated(client):
    response = client.post("/api/reference/vendors",
                           json={**VALID_VENDOR,
                                 "bank_account_last4": "123456789012"})
    assert response.status_code == 422
    assert "last 4 digits" in response.text


def test_a_missing_name_is_refused(client):
    response = client.post("/api/reference/vendors",
                           json={**VALID_VENDOR, "legal_name": "M"})
    assert response.status_code == 422


# ===========================================================================
# Adding a purchase order
# ===========================================================================
def test_a_value_only_po_can_be_raised(client):
    client.post("/api/reference/vendors", json=VALID_VENDOR)
    response = client.post("/api/reference/purchase-orders", json={
        "vendor_id": "V011", "total_amount": "87320.00",
        "issue_date": "2026-11-10", "note": "cleaning consumables",
    })
    assert response.status_code == 201, response.text

    added = response.json()
    assert added["po_number"] == "PO-4591"       # next free in the series
    assert added["lines"] == 0

    listed = client.get("/api/reference/purchase-orders").json()
    row = next(p for p in listed if p["po_number"] == "PO-4591")
    assert row["remaining_balance"] == "87320.00"
    assert row["status"] == "open"
    assert row["lines"] == []


def test_a_po_with_line_detail_computes_its_line_amounts(client):
    client.post("/api/reference/vendors", json=VALID_VENDOR)
    response = client.post("/api/reference/purchase-orders", json={
        "vendor_id": "V011", "po_number": "PO-4602", "total_amount": "87320.00",
        "lines": [{"description": "Industrial floor degreaser 20L",
                   "sku": "3402", "qty": "40", "unit_price": "1850.00"}],
    })
    assert response.status_code == 201

    listed = client.get("/api/reference/purchase-orders").json()
    row = next(p for p in listed if p["po_number"] == "PO-4602")
    assert len(row["lines"]) == 1
    assert row["lines"][0]["line_amount_display"] == "74,000.00"


def test_a_po_for_an_unknown_vendor_is_refused(client):
    response = client.post("/api/reference/purchase-orders", json={
        "vendor_id": "V999", "total_amount": "1000.00"})
    assert response.status_code == 422
    assert "Add the vendor first" in response.text


@pytest.mark.parametrize("total", ["0", "-500.00"])
def test_a_non_positive_po_total_is_refused(client, total):
    client.post("/api/reference/vendors", json=VALID_VENDOR)
    response = client.post("/api/reference/purchase-orders", json={
        "vendor_id": "V011", "total_amount": total})
    assert response.status_code == 422
    assert "greater than zero" in response.text


def test_a_duplicate_po_number_is_refused(client):
    client.post("/api/reference/vendors", json=VALID_VENDOR)
    response = client.post("/api/reference/purchase-orders", json={
        "vendor_id": "V011", "po_number": "PO-4501", "total_amount": "100.00"})
    assert response.status_code == 409


# ===========================================================================
# Re-run: the same document, decided against the reference data as it is now
# ===========================================================================
def start_meridian_run(client) -> str:
    """Run sample 09 through the pipeline with its recorded extraction."""
    from backend.app.pipeline.runner import execute_run, new_run_id

    from .test_persistence import StubExtractor

    data = Path("sample_invoices/09_unknown_vendor_meridian.pdf").read_bytes()
    run_id = new_run_id()
    outcome = execute_run(run_id, data, "09_unknown_vendor_meridian.pdf", "09",
                          extractor=StubExtractor("09_unknown_vendor_meridian"))
    assert outcome == "NEEDS_REVIEW"
    return run_id


def test_rerunning_after_onboarding_approves_the_same_invoice(client):
    """The demo flow, driven through HTTP exactly as the screen drives it."""
    run_id = start_meridian_run(client)

    held = client.get(f"/api/runs/{run_id}").json()
    assert held["outcome"] == "NEEDS_REVIEW"
    assert held["top_reason"] == "VENDOR_UNKNOWN"

    assert client.post("/api/reference/vendors",
                       json=VALID_VENDOR).status_code == 201
    assert client.post("/api/reference/purchase-orders", json={
        "vendor_id": "V011", "total_amount": "87320.00",
    }).status_code == 201

    response = client.post(f"/api/runs/{run_id}/rerun")
    assert response.status_code == 200, response.text
    assert response.json()["rerun_of"] == run_id

    new_id = response.json()["run_id"]
    rerun = client.get(f"/api/runs/{new_id}").json()
    assert rerun["outcome"] == "APPROVED", rerun["explanation"]
    assert rerun["vendor_id"] == "V011"
    assert rerun["po_number"] == "PO-4591"


def test_a_rerun_makes_no_new_model_call(client):
    """A re-run asks what we would decide now, not how the page reads today.

    Replaying the stored response keeps a re-run free and deterministic, and
    isolates the variable: if the outcome moves, the reference data moved.
    """
    run_id = start_meridian_run(client)
    before = client.get("/api/reference/llm-usage").json()["calls"]

    new_id = client.post(f"/api/runs/{run_id}/rerun").json()["run_id"]
    rerun = client.get(f"/api/runs/{new_id}").json()

    assert client.get("/api/reference/llm-usage").json()["calls"] == before
    assert rerun["llm_tokens"] == 0
    assert rerun["extraction"]["response"]["invoice_number"] == "MIS-2026-0188"


def test_a_rerun_is_not_treated_as_a_duplicate_of_itself(client):
    """The earlier run's ledger entry is withdrawn, not left to collide.

    Without this the re-run would match its own source by file hash and come
    back DUPLICATE_CONFIRMED_HASH - the same trap the demo-reset note covers.
    """
    run_id = start_meridian_run(client)
    new_id = client.post(f"/api/runs/{run_id}/rerun").json()["run_id"]

    rerun = client.get(f"/api/runs/{new_id}").json()
    found = {f["code"] for f in rerun["findings"]}
    assert "DUPLICATE_CONFIRMED_HASH" not in found
    assert "DUPLICATE_SUSPECTED" not in found

    ledger = client.get("/api/reference/ledger").json()
    assert sum(1 for row in ledger
               if row["invoice_number"] == "MIS-2026-0188") == 1


def test_rerunning_a_missing_run_is_a_404(client):
    assert client.post("/api/runs/does-not-exist/rerun").status_code == 404


def test_deleting_a_run_withdraws_its_ledger_entry(client):
    """Otherwise a deleted run keeps blocking its own invoice as a duplicate."""
    run_id = start_meridian_run(client)
    assert any(row["invoice_number"] == "MIS-2026-0188"
               for row in client.get("/api/reference/ledger").json())

    assert client.delete(f"/api/runs/{run_id}").status_code == 200
    assert not any(row["invoice_number"] == "MIS-2026-0188"
                   for row in client.get("/api/reference/ledger").json())


# ===========================================================================
# A vendor added here must be indistinguishable from a seeded one
# ===========================================================================
def test_an_added_vendor_is_matched_by_the_pipeline_like_any_other(client):
    """Including by alias, which is the whole reason aliases are editable."""
    from backend.app.db import get_engine
    from backend.app.pipeline.runner import load_reference
    from backend.app.rules.vendor import match_vendor
    from backend.app.rules.validation import validate_and_normalise
    from backend.app.config import get_rules
    from backend.app.domain import ExtractedInvoice

    client.post("/api/reference/vendors", json=VALID_VENDOR)

    rules = get_rules()
    with Session(get_engine()) as session:
        reference = load_reference(session)

    # The short form the invoice actually prints, and no GSTIN at all.
    invoice, _ = validate_and_normalise(ExtractedInvoice(
        vendor_name="Meridian Industrial Supplies", vendor_gstin=None,
        invoice_number="MIS-2026-0188", invoice_date="2026-11-12",
        total=D("87320.00")), rules)
    match, findings = match_vendor(invoice, reference.vendors, rules)

    assert match.vendor is not None
    assert match.vendor.vendor_id == "V011"
    assert "VENDOR_UNKNOWN" not in {f.code for f in findings}


def test_demo_reset_removes_vendors_added_on_screen(client):
    """A demo must start from a known state, including after onboarding."""
    client.post("/api/reference/vendors", json=VALID_VENDOR)
    assert len(client.get("/api/reference/vendors").json()) == 11

    assert client.post("/api/admin/reset").status_code == 200
    vendors = client.get("/api/reference/vendors").json()
    assert len(vendors) == 10
    assert all(v["vendor_id"] != "V011" for v in vendors)
