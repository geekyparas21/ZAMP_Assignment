"""Stage 3 - extraction.

The only place in the system that talks to a model, kept behind a small
interface so swapping provider is a one-file change.

Behaviour that matters for a live demo on a free tier:

  * exactly ONE successful call per run;
  * schema-constrained decoding, then Pydantic validation of the result anyway;
  * temperature 0;
  * retry with exponential backoff on transient errors;
  * on a rate limit, fall back to the lighter model before giving up;
  * on final failure, a specific reason code and a message a human can act on,
    never a silent empty extraction.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from ..config import Settings, get_settings
from ..domain import ExtractedInvoice
from .document import DocumentInfo
from .schema import EXTRACTION_PROMPT, InvoiceExtractionSchema

log = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Extraction failed after everything we could reasonably try."""

    def __init__(self, code: str, **evidence) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence


@dataclass
class ExtractionResult:
    invoice: ExtractedInvoice
    model: str
    attempts: int
    duration_ms: int
    prompt_tokens: int = 0
    response_tokens: int = 0
    total_tokens: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)
    input_kind: str = "text"
    warnings: list[str] = field(default_factory=list)
    #: True when this is a stored response being replayed rather than a live
    #: model call, so the quota counter is not credited with a call that never
    #: left the building.
    replayed: bool = False

    def redacted_log(self) -> dict[str, Any]:
        """What gets persisted to the run's step detail. No secrets."""
        return {
            "model": self.model, "attempts": self.attempts,
            "duration_ms": self.duration_ms, "input_kind": self.input_kind,
            "tokens": {"prompt": self.prompt_tokens, "response": self.response_tokens,
                       "total": self.total_tokens},
            "response": self.raw_response,
            "warnings": self.warnings,
            "replayed": self.replayed,
        }


class RecordedExtractor:
    """Replays an extraction that was already paid for.

    A re-run exists to ask "what would we decide now, with the reference data
    as it stands?" - not to re-read the document. Replaying the stored model
    response makes a re-run free, instant and deterministic, and it isolates
    the variable under test: if the outcome changes, it changed because the
    vendor master or the POs changed, not because the model read the page
    differently the second time.
    """

    def __init__(self, stored: dict[str, Any]) -> None:
        self._stored = stored or {}

    @property
    def usable(self) -> bool:
        return bool(self._stored.get("response"))

    def extract(self, document: DocumentInfo) -> ExtractionResult:
        stored = self._stored
        invoice, warnings = _parse(stored["response"])
        tokens = stored.get("tokens") or {}
        return ExtractionResult(
            invoice=invoice,
            model=stored.get("model", "recorded"),
            attempts=int(stored.get("attempts", 1) or 1),
            duration_ms=0,
            prompt_tokens=0, response_tokens=0, total_tokens=0,
            raw_response=stored["response"],
            input_kind=stored.get("input_kind", "text"),
            replayed=True,
            warnings=[*warnings, "Extraction replayed from the earlier run - "
                                 "no new model call was made."],
        )


class Extractor(Protocol):
    """Swap this out to change provider; nothing downstream needs to know."""

    def extract(self, document: DocumentInfo) -> ExtractionResult: ...


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return ("429" in text or "resource_exhausted" in text
            or "rate limit" in text or "quota" in text)


def _is_retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return _is_rate_limit(exc) or any(
        token in text for token in
        ("500", "502", "503", "504", "timeout", "timed out", "deadline",
         "unavailable", "internal", "connection", "temporarily"))


class GeminiExtractor:
    """Google Gemini, using structured output against InvoiceExtractionSchema."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.llm_configured:
            raise ExtractionError(
                "LLM_UNAVAILABLE", attempts=0,
                detail="GEMINI_API_KEY is not set. Add it to .env (local) or to "
                       "the host's environment variables (deployed).")
        from google import genai  # imported lazily so tests need no SDK

        self._genai = genai
        self._client = genai.Client(api_key=self.settings.gemini_api_key)

    # -- public ------------------------------------------------------------
    def extract(self, document: DocumentInfo) -> ExtractionResult:
        started = time.monotonic()
        models = [self.settings.gemini_model]
        if self.settings.gemini_fallback_model:
            models.append(self.settings.gemini_fallback_model)

        attempts = 0
        last_error: Exception | None = None
        rate_limited = False

        for model in models:
            for attempt in range(self.settings.gemini_max_retries):
                attempts += 1
                try:
                    payload, usage = self._call(model, document)
                    invoice, warnings = _parse(payload)
                    return ExtractionResult(
                        invoice=invoice, model=model, attempts=attempts,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        prompt_tokens=usage.get("prompt", 0),
                        response_tokens=usage.get("response", 0),
                        total_tokens=usage.get("total", 0),
                        raw_response=payload,
                        input_kind="page_images" if document.is_scanned else "text",
                        warnings=warnings,
                    )
                except ValidationError as exc:
                    # Malformed against our schema: worth one more try, since
                    # a re-roll at temperature 0 can still differ in structure.
                    last_error = exc
                    log.warning("extraction schema invalid (attempt %s): %s",
                                attempts, exc)
                    if attempt == self.settings.gemini_max_retries - 1:
                        break
                    _sleep(attempt)
                except Exception as exc:
                    last_error = exc
                    if _is_rate_limit(exc):
                        rate_limited = True
                        log.warning("rate limited on %s (attempt %s)", model, attempts)
                        break           # move to the lighter model immediately
                    if not _is_retryable(exc):
                        raise ExtractionError(
                            "LLM_UNAVAILABLE", attempts=attempts,
                            detail=_clean(exc)) from exc
                    log.warning("transient error on %s (attempt %s): %s",
                                model, attempts, exc)
                    _sleep(attempt)

        if isinstance(last_error, ValidationError):
            raise ExtractionError("EXTRACTION_SCHEMA_INVALID", attempts=attempts,
                                  detail=_clean(last_error))
        if rate_limited:
            raise ExtractionError("LLM_RATE_LIMITED", attempts=attempts,
                                  detail=_clean(last_error) if last_error else "")
        raise ExtractionError("LLM_UNAVAILABLE", attempts=attempts,
                              detail=_clean(last_error) if last_error else "unknown")

    # -- internals ----------------------------------------------------------
    def _call(self, model: str, document: DocumentInfo) -> tuple[dict, dict]:
        from google.genai import types

        parts: list[Any] = [EXTRACTION_PROMPT]
        if document.is_scanned:
            if not document.page_images:
                raise ExtractionError(
                    "LLM_UNAVAILABLE", attempts=0,
                    detail="the document has no text layer and no page images "
                           "could be rendered")
            parts.append(
                "This document has no text layer, so it is supplied as page "
                "images below. Read them carefully; the scan is imperfect.")
            for image in document.page_images:
                parts.append(types.Part.from_bytes(data=image, mime_type="image/png"))
        else:
            parts.append("Invoice text extracted from the PDF:\n\n"
                         f"---\n{document.text}\n---")

        response = self._client.models.generate_content(
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(
                # Determinism as far as the model allows it.
                temperature=0,
                response_mime_type="application/json",
                response_schema=InvoiceExtractionSchema,
                http_options=types.HttpOptions(
                    timeout=self.settings.gemini_timeout * 1000),
            ),
        )

        usage = {}
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            usage = {
                "prompt": getattr(meta, "prompt_token_count", 0) or 0,
                "response": getattr(meta, "candidates_token_count", 0) or 0,
                "total": getattr(meta, "total_token_count", 0) or 0,
            }

        text = (response.text or "").strip()
        if not text:
            raise ExtractionError("LLM_UNAVAILABLE", attempts=1,
                                  detail="the model returned an empty response")
        return json.loads(text), usage


def _parse(payload: dict) -> tuple[ExtractedInvoice, list[str]]:
    """Validate the payload and convert it, collecting non-fatal oddities."""
    model = InvoiceExtractionSchema.model_validate(payload)
    invoice = model.to_domain()

    warnings: list[str] = []
    if invoice.is_invoice and not invoice.lines:
        warnings.append("no line items were read from the document")
    if invoice.total is None:
        warnings.append("no payable total was read from the document")
    return invoice, warnings


def _sleep(attempt: int) -> None:
    """Exponential backoff with jitter, so retries do not thunder."""
    time.sleep(min(8.0, (2 ** attempt) + random.uniform(0, 0.4)))


def _clean(exc: Exception | None) -> str:
    """Error text for display, with anything key-shaped stripped out."""
    if exc is None:
        return ""
    text = str(exc)
    for marker in ("key=", "api_key=", "AIza"):
        index = text.find(marker)
        if index != -1:
            text = text[:index] + "[redacted]"
            break
    return text[:300]


def build_extractor(settings: Settings | None = None) -> Extractor:
    return GeminiExtractor(settings)
