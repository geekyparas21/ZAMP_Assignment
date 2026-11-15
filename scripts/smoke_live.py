"""Smoke-test a deployed instance by running real invoices through it.

The brief requires every scenario to pass on the deployed URL, not just
locally. Run this after a deploy, and again right before recording a demo.

    python scripts/smoke_live.py --base-url https://your-service.onrender.com
    python scripts/smoke_live.py --base-url ... --only 00 02a 02b 02c
    python scripts/smoke_live.py --base-url ... --reset   # start from a known state

Exits non-zero if any scenario does not produce its expected outcome.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORDER = [
    "00_happy_path_bharat_steel",
    "01_scanned_no_po_deccan",
    "01b_ambiguous_po_sunrise",
    "02a_split_po_nova_1of3",
    "02b_split_po_nova_2of3",
    "02c_split_po_nova_3of3_overbilled",
    "03_duplicate_vega",
    "03b_suspected_duplicate_suryodaya",
    "04a_within_tolerance_trident",
    "04b_price_creep_kaveri",
    "05_blocked_vendor_orient",
    "06_quotation_not_invoice",
    "07_missing_invoice_number_bharat",
    "08_bank_mismatch_bharat",
    "09_unknown_vendor_meridian",
    # The image path: a phone photo, which must approve like any other
    # invoice that matches its PO.
    "10_phone_photo_vega",
]


def get(url: str, timeout: int = 90):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def post(url: str, fields: dict | None = None, timeout: int = 90):
    data = urllib.parse.urlencode(fields).encode() if fields else b""
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def wait_for(base: str, run_id: str, timeout_s: int = 180) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = get(f"{base}/api/runs/{run_id}")
        if run["status"] != "running":
            return run
        time.sleep(1)
    return {"status": "TIMEOUT", "outcome": "TIMEOUT", "duration_ms": 0,
            "top_reason": "-", "id": run_id}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--only", nargs="*", default=None,
                        help="sample key prefixes, e.g. 00 02a")
    parser.add_argument("--reset", action="store_true",
                        help="reset demo data first (recommended)")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    # The first request to a sleeping free-tier host takes up to a minute.
    print(f"waking {base} ...", flush=True)
    started = time.time()
    try:
        health = get(f"{base}/health", timeout=120)
    except urllib.error.URLError as exc:
        raise SystemExit(f"could not reach {base}: {exc}")
    print(f"  up in {time.time() - started:.1f}s  "
          f"status={health['status']} database={health['database']} "
          f"({health['database_kind']}) model={health['model']}")
    if health["database_kind"] != "postgres":
        print("  WARNING: not using Postgres - run history will not survive a redeploy")

    if args.reset:
        print("resetting demo data ...")
        post(f"{base}/api/admin/reset")

    expected = {row["key"]: row["expected_outcome"] for row in get(f"{base}/api/samples")}
    keys = [k for k in ORDER
            if not args.only or any(k.startswith(p) for p in args.only)]

    print(f"\n{'sample':<38}{'expected':<14}{'actual':<14}{'ok':<6}{'ms':>7}  top reason")
    print("-" * 116)

    failures = []
    for key in keys:
        run_id = post(f"{base}/api/runs/sample", {"key": key})["run_id"]
        run = wait_for(base, run_id)
        ok = run["outcome"] == expected.get(key)
        if not ok:
            failures.append((key, expected.get(key), run["outcome"]))
        print(f"{key:<38}{expected.get(key, '?'):<14}{(run['outcome'] or '-'):<14}"
              f"{('OK' if ok else 'FAIL'):<6}{run['duration_ms'] or 0:>7}  "
              f"{run['top_reason'] or '-'}")

    stats = get(f"{base}/api/runs/stats")
    usage = get(f"{base}/api/reference/llm-usage")
    print(f"\n{stats['total_runs']} runs | approved {stats['by_outcome']['APPROVED']} "
          f"({stats['auto_approved_pct']}%) | review {stats['by_outcome']['NEEDS_REVIEW']} "
          f"| rejected {stats['by_outcome']['REJECTED']} | failed {stats['by_outcome']['FAILED']}")
    print(f"average {stats['avg_duration_ms']}ms | value approved INR "
          f"{stats['total_value_display']}")
    print(f"LLM calls in the last 24h: {usage['calls']} "
          f"({usage['failed_calls']} failed), {usage['total_tokens']} tokens")

    if failures:
        print("\nFAILURES:")
        for key, want, got in failures:
            print(f"  ! {key}: expected {want}, got {got}")
        sys.exit(1)
    print("\nAll scenarios produced their expected outcome on the deployed service.")


if __name__ == "__main__":
    main()
