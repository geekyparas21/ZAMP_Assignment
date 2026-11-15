"""Run real extraction over the sample invoices and save the results as fixtures.

Run this deliberately, not often: it is the only thing in the repo that spends
free-tier quota. The saved fixtures let the whole test suite run offline
afterwards, which is what keeps quota available for live demos.

    python scripts/extract_samples.py                 # all samples
    python scripts/extract_samples.py --only 00 01    # just these
    python scripts/extract_samples.py --dry-run       # no API calls
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from backend.app.config import SAMPLE_SUFFIXES, get_rules, get_settings  # noqa: E402
from backend.app.money import format_inr  # noqa: E402
from backend.app.services.document import analyse  # noqa: E402
from backend.app.services.extractor import (ExtractionError,  # noqa: E402
                                            GeminiExtractor)

SAMPLES = ROOT / "sample_invoices"
FIXTURES = ROOT / "tests" / "fixtures"


def serialise(result) -> dict:
    return {
        "model": result.model,
        "attempts": result.attempts,
        "input_kind": result.input_kind,
        "tokens": {"prompt": result.prompt_tokens,
                   "response": result.response_tokens,
                   "total": result.total_tokens},
        "warnings": result.warnings,
        "response": result.raw_response,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None,
                        help="filename prefixes, e.g. 00 03b")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the extraction path per sample, call nothing")
    args = parser.parse_args()

    rules = get_rules()
    settings = get_settings()
    FIXTURES.mkdir(parents=True, exist_ok=True)

    files = sorted(path for path in SAMPLES.iterdir()
                   if path.suffix.lower() in SAMPLE_SUFFIXES)
    if args.only:
        files = [p for p in files if any(p.name.startswith(k) for k in args.only)]
    if not files:
        raise SystemExit("no matching sample invoices")

    print(f"model: {settings.gemini_model}  fallback: {settings.gemini_fallback_model}")
    print(f"{len(files)} sample(s)\n")

    extractor = None
    if not args.dry_run:
        extractor = GeminiExtractor(settings)

    calls = 0
    tokens = 0
    failures: list[str] = []

    for source in files:
        data = source.read_bytes()
        document, _ = analyse(data, rules, source.name)
        label = f"{source.name:<40}"

        if args.dry_run:
            print(f"{label} {document.path_label:<16} "
                  f"{document.chars_per_page:>7} chars/page  "
                  f"{len(document.page_images)} image(s)")
            continue

        try:
            result = extractor.extract(document)
        except ExtractionError as exc:
            failures.append(f"{source.name}: {exc.code} {exc.evidence}")
            print(f"{label} FAILED  {exc.code}")
            continue

        calls += result.attempts
        tokens += result.total_tokens
        (FIXTURES / f"{source.stem}.json").write_text(
            json.dumps(serialise(result), indent=2), encoding="utf-8")

        inv = result.invoice
        print(f"{label} {result.input_kind:<12} {result.duration_ms:>6}ms  "
              f"{result.total_tokens:>6} tok  "
              f"{(inv.vendor_name or '-')[:26]:<26} "
              f"{(inv.invoice_number or '-'):<16} "
              f"PO={(inv.po_reference or '-'):<10} "
              f"{format_inr(inv.total):>14}")
        if result.warnings:
            for warning in result.warnings:
                print(f"{'':<40} ! {warning}")

    if args.dry_run:
        return

    print(f"\nAPI calls: {calls}   tokens: {tokens}")
    print(f"fixtures written to {FIXTURES}")
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  ! {failure}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
