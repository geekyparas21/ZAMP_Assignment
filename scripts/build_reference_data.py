"""
Build the reference dataset (vendors, purchase orders, PO lines, historical
ledger) as CSV files in data/.

Run once; the CSVs are committed and are the human-editable source of truth
that the application seeds from. This script exists so the dataset is
reproducible and so every GSTIN carries a genuine, verifiable checksum rather
than a plausible-looking string.

    python scripts/build_reference_data.py
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GST_RATE = Decimal("0.18")

# --------------------------------------------------------------------------
# GSTIN
# --------------------------------------------------------------------------
GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_digit(first_14: str) -> str:
    """Return the 15th (checksum) character of a GSTIN.

    The GSTIN checksum is a modulus-36 scheme: each of the first 14 characters
    is valued by its position in [0-9A-Z], multiplied by an alternating factor
    of 1 and 2, and the quotient and remainder of that product divided by 36
    are both added to a running total. The check digit is the character whose
    value makes the total a multiple of 36.
    """
    if len(first_14) != 14:
        raise ValueError(f"expected 14 characters, got {len(first_14)}")
    total = 0
    for i, ch in enumerate(first_14):
        value = GSTIN_ALPHABET.index(ch)
        product = value * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return GSTIN_ALPHABET[(36 - total % 36) % 36]


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    """Assemble a structurally valid GSTIN: state(2) + PAN(10) + entity + Z + checksum."""
    body = f"{state_code}{pan}{entity}Z"
    return body + gstin_check_digit(body)


# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------
# (vendor_id, legal_name, aliases, state_code, pan, status, terms, bank_last4)
VENDORS = [
    ("V001", "Bharat Steel Industries Private Limited",
     "Bharat Steel Ind.|Bharat Steel Industries|Bharat Steel",
     "27", "AABCB1234D", "approved", "Net 30", "4412"),
    ("V002", "Nova Polymers Private Limited",
     "Nova Polymers|NOVA POLYMERS PVT LTD|Nova Polymer",
     "29", "AACCN5678E", "approved", "Net 45", "7781"),
    ("V003", "Kaveri Logistics Services Private Limited",
     "Kaveri Logistics|Kaveri Logistics Svcs",
     "33", "AADCK9012F", "approved", "Net 15", "2290"),
    ("V004", "Deccan Precision Tools Private Limited",
     "Deccan Precision Tools|Deccan Precision|Deccan Tools",
     "36", "AAECD3456G", "approved", "Net 30", "5533"),
    ("V005", "Sunrise Packaging Company",
     "Sunrise Packaging|Sunrise Packaging Co.",
     "24", "AAFCS7890H", "approved", "Net 30", "9021"),
    ("V006", "Orient Electricals Limited",
     "Orient Electricals|Orient Electrical Ltd",
     "07", "AAGCO2345J", "blocked", "Net 30", "1187"),
    ("V007", "Himalaya Chemicals Private Limited",
     "Himalaya Chemicals|Himalaya Chem",
     "06", "AAHCH6789K", "pending", "Net 60", "3344"),
    ("V008", "Vega Instruments Private Limited",
     "Vega Instruments|Vega Instrument Pvt Ltd",
     "19", "AAICV0123L", "approved", "Net 30", "6655"),
    ("V009", "Trident Fasteners and Hardware Private Limited",
     "Trident Fasteners|Trident Fasteners & Hardware",
     "23", "AAJCT4567M", "approved", "Net 30", "8809"),
    ("V010", "Suryodaya Engineering Works Private Limited",
     "Suryodaya Engineering|Suryodaya Engg Works",
     "08", "AAKCS8901N", "approved", "Net 45", "4470"),
]

# --------------------------------------------------------------------------
# Purchase orders
#
# CONVENTION: po.total_amount is the committed value INCLUSIVE of GST, so that
# it is directly comparable with an invoice grand total. po_lines carry
# PRE-TAX unit prices, which is what line-level price variance is measured on.
# --------------------------------------------------------------------------
# (po_number, vendor_id, issue_date, status, lines[(sku, description, qty, unit_price)],
#  amount_invoiced_to_date, note)
PURCHASE_ORDERS = [
    # --- Scenario 0: happy path (Bharat Steel, explicit PO on the invoice) ---
    ("PO-4501", "V001", "2026-10-02", "open", [
        ("MS-PLT-10", "MS Plate 10mm IS2062 Gr.B", "120", "485.00"),
        ("MS-ANG-5050", "MS Angle 50x50x5mm", "200", "162.50"),
    ], "0.00", "scenario 0 - happy path"),

    # --- Scenario 2: split PO across three invoices, third over-bills ---
    ("PO-4510", "V002", "2026-09-18", "open", [
        ("PP-H110", "Polypropylene granules PP-H110 (kg)", "5000", "85.00"),
    ], "0.00", "scenario 2 - split PO, 5000kg committed"),

    # --- Scenario 1: scanned invoice with no PO reference, inferred ---
    ("PO-4520", "V004", "2026-10-20", "open", [
        ("CNMG120408", "Carbide turning insert CNMG120408", "500", "212.00"),
        ("PCLNR2525", "Tool holder PCLNR2525M12", "10", "3450.00"),
    ], "0.00", "scenario 1 - unique open PO, inferred from amount+lines"),

    # --- Scenario 1b: two near-identical open POs -> ambiguous inference ---
    ("PO-4521", "V005", "2026-10-28", "open", [
        ("BOX-5PLY-A", "Corrugated box 5-ply 600x400x300mm", "3000", "46.00"),
    ], "0.00", "scenario 1b - ambiguous candidate A"),
    ("PO-4522", "V005", "2026-11-03", "open", [
        ("BOX-5PLY-A", "Corrugated box 5-ply 600x400x300mm", "3050", "46.00"),
    ], "0.00", "scenario 1b - ambiguous candidate B"),

    # --- Scenario 4a: within-tolerance variance (freight on the invoice) ---
    ("PO-4530", "V009", "2026-10-25", "open", [
        ("HEX-M12X50", "Hex bolt M12x50 Gr.8.8 zinc plated", "10000", "11.50"),
    ], "0.00", "scenario 4a - freight adds Rs 180, inside tolerance"),

    # --- Scenario 4b: 8% unit price creep on one line ---
    ("PO-4531", "V003", "2026-10-14", "open", [
        ("FRT-PUN-MAA", "Road freight Pune to Chennai, per trip", "20", "8500.00"),
    ], "0.00", "scenario 4b - invoice raises rate to 9180.00"),

    # --- Scenario 3: confirmed duplicate (already fully billed) ---
    ("PO-4540", "V008", "2026-09-30", "closed", [
        ("PG-DIG-100", "Digital pressure gauge 0-100 bar", "40", "2000.00"),
    ], "94400.00", "scenario 3 - fully billed by INV-2026-0042"),

    # --- Scenario 3b: suspected duplicate (half billed, room remains) ---
    ("PO-4550", "V010", "2026-10-08", "open", [
        ("BRK-ASSY-7", "Fabricated bracket assembly type-7", "300", "410.00"),
    ], "72570.00", "scenario 3b - one delivery billed, one outstanding"),

    # --- Supporting / realism POs -----------------------------------------
    ("PO-4505", "V001", "2026-08-12", "closed", [
        ("MS-CHNL-100", "MS Channel 100x50mm", "150", "720.00"),
    ], "127440.00", "closed PO - rejects a late invoice"),
    ("PO-4560", "V007", "2026-11-02", "open", [
        ("CHM-NAOH-50", "Caustic soda flakes 50kg bag", "200", "2150.00"),
    ], "0.00", "pending vendor"),
    ("PO-4570", "V006", "2026-10-11", "open", [
        ("CBL-4C-25", "4-core armoured cable 25sqmm (m)", "800", "412.00"),
    ], "0.00", "blocked vendor"),
    ("PO-4580", "V008", "2026-11-05", "open", [
        ("FLW-MTR-DN50", "Electromagnetic flow meter DN50", "6", "18500.00"),
    ], "0.00", "open, unused"),
    ("PO-4590", "V001", "2026-11-06", "open", [
        ("MS-RND-20", "MS Round bar 20mm", "500", "92.00"),
    ], "0.00", "open, unused"),
]

# --------------------------------------------------------------------------
# Historical invoice ledger (already-processed invoices)
# --------------------------------------------------------------------------
# (invoice_id, vendor_id, invoice_number, invoice_date, total, po_number, outcome, processed_at)
LEDGER = [
    # Scenario 3 target: a re-send formatted as INV/2026/42 must match this.
    ("L-0001", "V008", "INV-2026-0042", "2026-10-15", "94400.00", "PO-4540",
     "APPROVED", "2026-10-16T09:12:00"),
    # Scenario 3b target: same vendor + same amount, different number, recent.
    ("L-0002", "V010", "SEW-2026-118", "2026-11-10", "72570.00", "PO-4550",
     "APPROVED", "2026-11-10T14:41:00"),
    # Background history, so the dashboard and duplicate checks are not empty.
    ("L-0003", "V001", "BSI-2026-0881", "2026-08-20", "127440.00", "PO-4505",
     "APPROVED", "2026-08-21T11:05:00"),
    ("L-0004", "V003", "KLS-2026-0455", "2026-09-05", "88500.00", "",
     "APPROVED", "2026-09-06T10:20:00"),
    ("L-0005", "V005", "SPC-2026-0310", "2026-09-22", "61360.00", "",
     "APPROVED", "2026-09-23T16:02:00"),
    ("L-0006", "V004", "DPT-2026-0199", "2026-10-01", "45820.00", "",
     "NEEDS_REVIEW", "2026-10-02T09:47:00"),
    ("L-0007", "V002", "NPL-2026-0677", "2026-09-12", "118000.00", "",
     "APPROVED", "2026-09-13T13:30:00"),
]


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # --- vendors.csv ---
    vendor_gstins: dict[str, str] = {}
    with (DATA_DIR / "vendors.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["vendor_id", "legal_name", "aliases", "gstin", "status",
                    "payment_terms", "bank_account_last4"])
        for vid, name, aliases, state, pan, status, terms, bank in VENDORS:
            gstin = make_gstin(state, pan)
            vendor_gstins[vid] = gstin
            w.writerow([vid, name, aliases, gstin, status, terms, bank])

    # --- purchase_orders.csv + po_lines.csv ---
    with (DATA_DIR / "purchase_orders.csv").open("w", newline="", encoding="utf-8") as po_fh, \
         (DATA_DIR / "po_lines.csv").open("w", newline="", encoding="utf-8") as ln_fh:
        pw = csv.writer(po_fh)
        lw = csv.writer(ln_fh)
        pw.writerow(["po_number", "vendor_id", "issue_date", "status", "currency",
                     "total_amount", "amount_invoiced_to_date", "note"])
        lw.writerow(["po_number", "line_no", "sku", "description", "qty", "unit_price",
                     "line_amount"])

        for po_number, vid, issue_date, status, lines, invoiced, note in PURCHASE_ORDERS:
            pre_tax = Decimal("0.00")
            for idx, (sku, desc, qty, price) in enumerate(lines, start=1):
                amount = (Decimal(qty) * Decimal(price)).quantize(Decimal("0.01"))
                pre_tax += amount
                lw.writerow([po_number, idx, sku, desc, qty, price, money(amount)])
            total = (pre_tax * (1 + GST_RATE)).quantize(Decimal("0.01"))
            pw.writerow([po_number, vid, issue_date, status, "INR",
                         money(total), money(Decimal(invoiced)), note])

    # --- invoice_ledger.csv ---
    with (DATA_DIR / "invoice_ledger.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["invoice_id", "vendor_id", "invoice_number", "invoice_date",
                    "currency", "total_amount", "po_number", "outcome", "processed_at",
                    "file_sha256"])
        for lid, vid, number, date, total, po, outcome, processed in LEDGER:
            w.writerow([lid, vid, number, date, "INR", total, po, outcome, processed, ""])

    # --- report ---
    print(f"Wrote reference data to {DATA_DIR}")
    print(f"  vendors.csv          {len(VENDORS)} vendors")
    print(f"  purchase_orders.csv  {len(PURCHASE_ORDERS)} POs")
    print(f"  po_lines.csv         {sum(len(p[4]) for p in PURCHASE_ORDERS)} lines")
    print(f"  invoice_ledger.csv   {len(LEDGER)} historical invoices")
    print()
    print("GSTIN check (state + PAN + entity + Z + computed checksum):")
    for vid, gstin in vendor_gstins.items():
        ok = gstin_check_digit(gstin[:14]) == gstin[14]
        print(f"  {vid}  {gstin}  checksum {'valid' if ok else 'INVALID'}")


if __name__ == "__main__":
    main()
