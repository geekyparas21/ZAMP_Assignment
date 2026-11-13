"""Print decision cards for the scenarios, straight from the rules engine.

No LLM, no database, no web server - this runs the real deterministic stages
against the real reference data and prints what an AP clerk would see. Useful
for checking explainability without starting the app.

    python scripts/demo_decision.py
    python scripts/demo_decision.py --scenario 2
"""

from __future__ import annotations

import argparse
import copy
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.domain import ExtractedInvoice, ExtractedLine  # noqa: E402
from backend.app.money import format_inr  # noqa: E402
from backend.app.reference import (load_ledger, load_purchase_orders,  # noqa: E402
                                   load_vendors)
from backend.app.rules.evaluate import (ReferenceData, apply_approval,  # noqa: E402
                                        evaluate)

D = Decimal
RULE = "=" * 78


def base(**overrides) -> ExtractedInvoice:
    defaults = dict(
        is_invoice=True, document_type="invoice", currency="INR",
        tax_rate_pct=D("18"),
        field_confidence={"vendor_name": 0.97, "vendor_gstin": 0.96,
                          "invoice_number": 0.98, "invoice_date": 0.97,
                          "total": 0.99},
    )
    defaults.update(overrides)
    return ExtractedInvoice(**defaults)


def nova(qty: Decimal, number: str, invoice_date: str) -> ExtractedInvoice:
    amount = (qty * D("85.00")).quantize(D("0.01"))
    tax = (amount * D("0.18")).quantize(D("0.01"))
    return base(
        vendor_name="Nova Polymers Pvt Ltd", vendor_gstin="29AACCN5678E1Z5",
        invoice_number=number, invoice_date=invoice_date, due_date="2026-12-17",
        po_reference="PO-4510", bank_account_last4="7781",
        lines=[ExtractedLine("Polypropylene granules PP-H110", "3902", qty, "KG",
                             D("85.00"), amount)],
        subtotal=amount, tax_total=tax, total=(amount + tax).quantize(D("0.01")))


SCENARIOS = {
    "0": ("Happy path - Bharat Steel, explicit PO, exact match", [base(
        vendor_name="Bharat Steel Ind.", vendor_gstin="27AABCB1234D1ZC",
        invoice_number="BSI-2026-1042", invoice_date="2026-11-04",
        due_date="2026-12-04", po_reference="PO-4501", bank_account_last4="4412",
        lines=[
            ExtractedLine("MS Plate 10mm IS2062 Gr.B", "7208", D("120"), "NOS",
                          D("485.00"), D("58200.00")),
            ExtractedLine("MS Angle 50x50x5mm", "7216", D("200"), "NOS",
                          D("162.50"), D("32500.00")),
        ],
        subtotal=D("90700.00"), tax_total=D("16326.00"), total=D("107026.00"))]),

    "1": ("Scanned invoice, no PO quoted - PO inferred", [base(
        vendor_name="Deccan Precision Tools Pvt. Ltd.",
        vendor_gstin="36AAECD3456G1ZQ", invoice_number="DPT/2026/0247",
        invoice_date="2026-11-05", due_date="2026-12-05", po_reference=None,
        bank_account_last4="5533",
        lines=[
            ExtractedLine("Carbide turning insert CNMG120408", "8209", D("500"),
                          "PCS", D("212.00"), D("106000.00")),
            ExtractedLine("Tool holder PCLNR2525M12", "8466", D("10"), "NOS",
                          D("3450.00"), D("34500.00")),
        ],
        subtotal=D("140500.00"), tax_total=D("25290.00"), total=D("165790.00"))]),

    "1b": ("No PO quoted, two near-identical open POs - ambiguous", [base(
        vendor_name="Sunrise Packaging Co.", vendor_gstin="24AAFCS7890H1ZZ",
        invoice_number="SPC-2026-0417", invoice_date="2026-11-07",
        due_date="2026-12-07", po_reference=None, bank_account_last4="9021",
        lines=[ExtractedLine("Corrugated box 5-ply 600x400x300mm", "4819",
                             D("3020"), "NOS", D("46.00"), D("138920.00"))],
        subtotal=D("138920.00"), tax_total=D("25005.60"),
        total=D("163925.60"))]),

    "2": ("Split PO - three invoices against PO-4510, run in sequence", [
        nova(D("2000"), "NPL-2026-0712", "2026-11-02"),
        nova(D("2000"), "NPL-2026-0736", "2026-11-08"),
        nova(D("1500"), "NPL-2026-0759", "2026-11-12"),
    ]),

    "3": ("Disguised duplicate - INV/2026/42 vs the paid INV-2026-0042", [base(
        vendor_name="Vega Instruments Pvt Ltd", vendor_gstin="19AAICV0123L1Z7",
        invoice_number="INV/2026/42", invoice_date="2026-10-15",
        due_date="2026-11-14", po_reference="PO-4540", bank_account_last4="6655",
        lines=[ExtractedLine("Digital pressure gauge 0-100 bar", "9026", D("40"),
                             "NOS", D("2000.00"), D("80000.00"))],
        subtotal=D("80000.00"), tax_total=D("14400.00"), total=D("94400.00"))]),

    "3b": ("Suspected duplicate, GST-inclusive rates", [base(
        vendor_name="Suryodaya Engg Works", vendor_gstin="08AAKCS8901N1ZP",
        invoice_number="SEW-2026-131", invoice_date="2026-11-12",
        due_date="2026-12-27", po_reference="PO-4550", bank_account_last4="4470",
        lines=[ExtractedLine("Fabricated bracket assembly type-7", "7308",
                             D("150"), "NOS", D("483.80"), D("72570.00"))],
        subtotal=None, tax_total=None, total=D("72570.00"),
        tax_inclusive=True)]),

    "4a": ("Freight of Rs 180 - inside tolerance", [base(
        vendor_name="Trident Fasteners & Hardware", vendor_gstin="23AAJCT4567M1ZT",
        invoice_number="TFH-2026-2288", invoice_date="2026-11-09",
        due_date="2026-12-09", po_reference="PO-4530", bank_account_last4="8809",
        lines=[
            ExtractedLine("Hex bolt M12x50 Gr.8.8 zinc plated", "7318",
                          D("10000"), "NOS", D("11.50"), D("115000.00")),
            ExtractedLine("Freight & handling charges", None, None, None, None,
                          D("152.54")),
        ],
        subtotal=D("115152.54"), tax_total=D("20727.46"),
        total=D("135880.00"))]),

    "4b": ("Unit rate raised 8% above the PO", [base(
        vendor_name="Kaveri Logistics Services", vendor_gstin="33AADCK9012F1ZW",
        invoice_number="KLS-2026-0588", invoice_date="2026-11-10",
        due_date="2026-11-25", po_reference="PO-4531", bank_account_last4="2290",
        lines=[ExtractedLine("Road freight Pune to Chennai, per trip", "9965",
                             D("20"), "TRIP", D("9180.00"), D("183600.00"))],
        subtotal=D("183600.00"), tax_total=D("33048.00"),
        total=D("216648.00"))]),
}


def fresh_reference() -> ReferenceData:
    return ReferenceData(
        vendors=copy.deepcopy(load_vendors()),
        purchase_orders=copy.deepcopy(load_purchase_orders()),
        ledger=copy.deepcopy(load_ledger()),
    )


def print_card(result, index: int) -> None:
    decision = result.decision
    print(f"\n  OUTCOME: {decision.outcome}")
    print(f"  Vendor:  {result.vendor.legal_name if result.vendor else '(not matched)'}")
    po = result.matched_po
    print(f"  PO:      {po.po_number if po else '(none matched)'}"
          + (f"   remaining {format_inr(po.remaining_balance)}" if po else ""))
    if result.invoice and result.invoice.total is not None:
        print(f"  Amount:  INR {format_inr(result.invoice.total)}")

    print("\n  Why:")
    for finding in decision.findings:
        print(f"    [{finding.severity.name:<6}] {finding.code}")
        print(f"             {finding.message}")

    print(f"\n  Explanation: {decision.explanation}")
    print(f"  Next action: {decision.next_action}")

    if decision.draft_email:
        print(f"\n  Draft email to {decision.draft_email['to']}")
        print(f"    Subject: {decision.draft_email['subject']}")
        for line in decision.draft_email["body"].splitlines():
            print(f"    | {line}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default=None,
                        help="one of: " + ", ".join(SCENARIOS))
    args = parser.parse_args()

    wanted = [args.scenario] if args.scenario else list(SCENARIOS)
    for key in wanted:
        if key not in SCENARIOS:
            raise SystemExit(f"unknown scenario {key!r}; try {', '.join(SCENARIOS)}")
        label, extractions = SCENARIOS[key]
        reference = fresh_reference()

        print(f"\n{RULE}\nSCENARIO {key} - {label}\n{RULE}")
        for index, extraction in enumerate(extractions, start=1):
            if len(extractions) > 1:
                print(f"\n--- invoice {index} of {len(extractions)}: "
                      f"{extraction.invoice_number} ---")
            result = evaluate(extraction, reference, f"demo-hash-{key}-{index}")
            print_card(result, index)

            effect = apply_approval(result)
            if effect:
                print(f"\n  Ledger effect: {effect['po_number']} invoiced-to-date "
                      f"{effect['invoiced_before']} -> {effect['invoiced_after']} "
                      f"(remaining {effect['remaining_after']})")
                po = next(p for p in reference.purchase_orders
                          if p.po_number == effect["po_number"])
                po.amount_invoiced_to_date = effect["_raw_after"]
    print()


if __name__ == "__main__":
    main()
