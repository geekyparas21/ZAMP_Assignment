"""Extraction, tested against recorded output from the real model.

The fixtures in tests/fixtures/ are genuine Gemini responses to the sample
PDFs, captured once by scripts/extract_samples.py. Testing against them keeps
the suite deterministic and offline - free-tier quota stays available for live
demos - while still proving the pipeline works on what the model actually
returns rather than on something hand-written to be convenient.

The opt-in live test at the bottom calls the real API: `pytest -m live`.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.rules.codes import Finding
from backend.app.rules.evaluate import ReferenceData, apply_approval, evaluate
from backend.app.services.extractor import _is_rate_limit, _is_retryable, _parse
from backend.app.services.schema import InvoiceExtractionSchema

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLES = Path(__file__).resolve().parents[1] / "sample_invoices"
D = Decimal


def load_fixture(stem: str) -> dict:
    return json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))


def extracted(stem: str):
    """The domain object the pipeline would receive for this sample."""
    return _parse(load_fixture(stem)["response"])[0]


@pytest.fixture
def reference(vendors, purchase_orders, ledger):
    return ReferenceData(copy.deepcopy(vendors), copy.deepcopy(purchase_orders),
                         copy.deepcopy(ledger))


@pytest.fixture(scope="session")
def manifest():
    return {row["file"]: row for row in
            json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))}


ALL_STEMS = sorted(p.stem for p in FIXTURES.glob("*.json"))


# ===========================================================================
# The fixtures themselves
# ===========================================================================
def test_a_fixture_exists_for_every_sample():
    samples = {p.stem for p in SAMPLES.glob("*.pdf")}
    assert samples == set(ALL_STEMS), "run scripts/extract_samples.py"


@pytest.mark.parametrize("stem", ALL_STEMS)
def test_every_recorded_response_still_validates(stem):
    """Guards the schema: tightening it must not orphan the fixtures."""
    InvoiceExtractionSchema.model_validate(load_fixture(stem)["response"])


@pytest.mark.parametrize("stem", ALL_STEMS)
def test_extraction_read_the_total_the_invoice_was_built_with(stem, manifest):
    expected = D(manifest[f"{stem}.pdf"]["total_raw"])
    assert extracted(stem).total == expected


@pytest.mark.parametrize("stem", ALL_STEMS)
def test_the_supplier_was_read_not_the_buyer(stem):
    """The easiest extraction mistake on an invoice, and the costliest."""
    invoice = extracted(stem)
    assert invoice.vendor_name
    assert "acme" not in invoice.vendor_name.lower()
    if invoice.vendor_gstin:
        assert invoice.vendor_gstin != "27AAACA1111A1Z8"      # the buyer's GSTIN


def test_the_scanned_sample_was_read_from_images():
    fixture = load_fixture("01_scanned_no_po_deccan")
    assert fixture["input_kind"] == "page_images"
    invoice = extracted("01_scanned_no_po_deccan")
    assert invoice.total == D("165790.00")
    assert len(invoice.lines) == 2, "both line items must survive the scan"


def test_a_missing_po_is_returned_as_null_not_invented():
    """The model is told never to infer a PO. Inference is the engine's job."""
    assert extracted("01_scanned_no_po_deccan").po_reference is None
    assert extracted("01b_ambiguous_po_sunrise").po_reference is None


def test_a_missing_invoice_number_is_returned_as_null():
    assert extracted("07_missing_invoice_number_bharat").invoice_number is None


def test_the_invoice_number_is_preserved_exactly_as_printed():
    """If the model tidied this, the duplicate scenario would not work."""
    assert extracted("03_duplicate_vega").invoice_number == "INV/2026/42"


def test_a_quotation_is_identified_as_not_an_invoice():
    invoice = extracted("06_quotation_not_invoice")
    assert invoice.is_invoice is False
    assert "quot" in (invoice.document_type or "").lower()


def test_gst_inclusive_presentation_is_detected():
    assert extracted("03b_suspected_duplicate_suryodaya").tax_inclusive is True


def test_the_altered_bank_account_was_read_from_the_invoice():
    assert extracted("08_bank_mismatch_bharat").bank_account_last4 == "9931"


# ===========================================================================
# Real model output -> real rules -> expected decision
# ===========================================================================
SEQUENCED = {"02b_split_po_nova_2of3", "02c_split_po_nova_3of3_overbilled"}


@pytest.mark.parametrize("stem", [s for s in ALL_STEMS if s not in SEQUENCED])
def test_each_sample_reaches_its_expected_outcome(stem, reference, manifest):
    row = manifest[f"{stem}.pdf"]
    prior = ([Finding("SCANNED_DOCUMENT", "document_analysis",
                      {"chars": 0, "pages": 1})]
             if row["kind"] == "scanned" else [])

    result = evaluate(extracted(stem), reference, f"hash-{stem}",
                      prior_findings=prior)

    assert result.outcome == row["expected_outcome"], \
        f"{stem}: {result.outcome} != {row['expected_outcome']} ({result.codes})"
    for code in row["expected_reasons"]:
        assert code in result.codes, f"{stem}: expected {code}, got {result.codes}"


def test_the_split_po_sequence_from_real_extractions(reference):
    """Scenario 2 end-to-end on genuine model output, run in order."""
    po = next(p for p in reference.purchase_orders if p.po_number == "PO-4510")

    first = evaluate(extracted("02a_split_po_nova_1of3"), reference, "h-2a")
    assert first.outcome == "APPROVED"
    po.amount_invoiced_to_date = apply_approval(first)["_raw_after"]

    second = evaluate(extracted("02b_split_po_nova_2of3"), reference, "h-2b")
    assert second.outcome == "APPROVED"
    po.amount_invoiced_to_date = apply_approval(second)["_raw_after"]
    assert po.remaining_balance == D("120360.00")

    third = evaluate(extracted("02c_split_po_nova_3of3_overbilled"), reference, "h-2c")
    assert third.outcome == "NEEDS_REVIEW"
    assert "PO_OVERBILLED" in third.codes
    evidence = next(f for f in third.findings if f.code == "PO_OVERBILLED").evidence
    assert evidence["overage"] == "30,090.00"


def test_every_decision_from_real_output_can_explain_itself(reference, manifest):
    for stem in ALL_STEMS:
        if stem in SEQUENCED:
            continue
        result = evaluate(extracted(stem), reference, f"x-{stem}")
        assert result.decision.explanation.strip(), stem
        assert result.decision.next_action.strip(), stem
        if result.outcome in ("NEEDS_REVIEW", "REJECTED"):
            assert result.decision.draft_email, stem


# ===========================================================================
# Schema coercion - models are inconsistent about types
# ===========================================================================
@pytest.mark.parametrize("raw,expected", [
    ("107026.00", D("107026.00")),
    ("1,07,026.00", D("107026.00")),
    ("INR 107026", D("107026")),
    ("₹107026.00", D("107026.00")),
    (107026, D("107026")),
    (107026.5, D("107026.5")),
])
def test_amounts_are_coerced_however_the_model_writes_them(raw, expected):
    model = InvoiceExtractionSchema.model_validate({"total": str(raw)})
    assert model.to_domain().total == expected


@pytest.mark.parametrize("raw", ["not a number", "", "N/A", "see attached"])
def test_a_non_numeric_amount_becomes_null_not_zero(raw):
    """A silent zero in an invoice total would be a payment bug."""
    model = InvoiceExtractionSchema.model_validate({"total": raw})
    assert model.to_domain().total is None


@pytest.mark.parametrize("raw,expected", [
    ("4412", "4412"),
    ("XXXXXXXX4412", "4412"),
    ("5010 1234 5678 4412", "4412"),
    ("12", None),
])
def test_bank_account_is_reduced_to_the_last_four_digits(raw, expected):
    model = InvoiceExtractionSchema.model_validate({"bank_account_last4": raw})
    assert model.bank_account_last4 == expected


def test_an_empty_response_validates_to_an_empty_invoice():
    """A blank response must not crash; downstream flags the missing fields."""
    invoice, warnings = _parse({})
    assert invoice.total is None
    assert invoice.lines == []
    assert warnings


def test_gstin_is_tidied_but_not_corrected():
    model = InvoiceExtractionSchema.model_validate(
        {"vendor_gstin": " 27aabcb1234d1zc "})
    assert model.vendor_gstin == "27AABCB1234D1ZC"


# ===========================================================================
# Failure classification
# ===========================================================================
@pytest.mark.parametrize("message", [
    "429 RESOURCE_EXHAUSTED", "Quota exceeded for quota metric",
    "rate limit reached, retry later",
])
def test_rate_limits_are_recognised(message):
    assert _is_rate_limit(Exception(message))
    assert _is_retryable(Exception(message))


@pytest.mark.parametrize("message", [
    "503 Service Unavailable", "deadline exceeded", "connection reset",
    "500 Internal error",
])
def test_transient_failures_are_retried(message):
    assert _is_retryable(Exception(message))
    assert not _is_rate_limit(Exception(message))


@pytest.mark.parametrize("message", [
    "400 INVALID_ARGUMENT: bad request", "403 permission denied",
])
def test_permanent_failures_are_not_retried(message):
    assert not _is_retryable(Exception(message))


# ===========================================================================
# Live - opt in with `pytest -m live`, costs quota
# ===========================================================================
@pytest.mark.live
def test_live_extraction_against_the_real_api(rules):
    from dotenv import load_dotenv
    load_dotenv()

    from backend.app.config import Settings
    from backend.app.services.document import analyse
    from backend.app.services.extractor import GeminiExtractor

    settings = Settings()
    if not settings.llm_configured:
        pytest.skip("GEMINI_API_KEY is not set")

    data = (SAMPLES / "00_happy_path_bharat_steel.pdf").read_bytes()
    document, _ = analyse(data, rules)
    result = GeminiExtractor(settings).extract(document)

    assert result.invoice.total == D("107026.00")
    assert result.invoice.po_reference == "PO-4501"
    assert result.attempts == 1, "the happy path must not need a retry"
    assert result.total_tokens > 0
