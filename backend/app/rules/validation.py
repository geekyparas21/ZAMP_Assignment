"""Stage 4 - validation and normalisation.

Turns untrusted model output into a NormalisedInvoice, and emits findings for
anything that does not add up. Nothing here calls an LLM; every function is a
pure transformation that a test can pin down exactly.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..config import Rules
from ..money import format_inr
from ..domain import ExtractedInvoice, NormalisedInvoice
from .codes import Finding

STAGE = "validation"
CENT = Decimal("0.01")

# Date formats seen on Indian vendor invoices, most specific first.
_DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%d-%b-%Y", "%d-%B-%Y", "%m/%d/%Y",
]

_GSTIN_RE = re.compile(r"^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ---------------------------------------------------------------------------
# Scalar normalisers
# ---------------------------------------------------------------------------
def normalise_invoice_number(raw: str | None) -> str | None:
    """Reduce an invoice number to a comparable key.

    Vendors re-send the same invoice under cosmetically different numbering:
    'INV-2026-0042', 'INV/2026/42' and 'inv 2026 42' are one invoice. The key
    strips separators, upper-cases, and drops leading zeros inside each numeric
    run so that 0042 and 42 collapse together.
    """
    if not raw:
        return None
    text = raw.strip().upper()
    parts = re.split(r"[^A-Z0-9]+", text)
    out = []
    for part in parts:
        if not part:
            continue
        # Split letter/digit runs so 'INV0042' -> 'INV' + '0042'
        for run in re.findall(r"[A-Z]+|[0-9]+", part):
            out.append(run.lstrip("0") or "0" if run.isdigit() else run)
    return "".join(out) or None


def normalise_po_reference(raw: str | None) -> str | None:
    """Reduce a PO reference to a comparable key.

    'PO-4501', 'po 4501', 'P.O. #4501' and 'Our Ref: PO4501' all become
    'PO4501'. Common prefixes vendors type in front of the number are dropped.
    """
    if not raw:
        return None
    text = raw.strip().upper()
    text = re.sub(r"\b(OUR|YOUR|CUSTOMER|BUYER)?\s*(REF(ERENCE)?|ORDER)\s*(NO\.?|#|:)?", " ", text)
    text = re.sub(r"[^A-Z0-9]+", "", text)
    if not text:
        return None
    m = re.search(r"(PO)?(\d{3,})", text)
    if m:
        return f"PO{m.group(2)}"
    return text


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(raw) -> Decimal | None:
    """Parse an amount written the way invoices write them.

    Handles Indian digit grouping ('1,07,026.00'), currency symbols and
    trailing credit markers. Returns None rather than guessing when the text
    is not a number.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw)).quantize(CENT)
        except InvalidOperation:
            return None
    text = str(raw).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.upper().endswith("CR")
    text = re.sub(r"(?i)\b(INR|RS\.?|CR|DR)\b", "", text)
    text = text.replace("₹", "").replace(",", "").strip(" ()")
    if not re.fullmatch(r"-?\d*\.?\d+", text):
        return None
    try:
        value = Decimal(text).quantize(CENT)
    except InvalidOperation:
        return None
    return -value if negative and value > 0 else value


def gstin_problem(gstin: str | None) -> str | None:
    """Return why a GSTIN is invalid, or None if it is fine."""
    if not gstin:
        return None
    text = gstin.strip().upper().replace(" ", "")
    if len(text) != 15:
        return f"expected 15 characters, found {len(text)}"
    if not _GSTIN_RE.match(text):
        return "does not match the GSTIN pattern"
    total = 0
    for i, ch in enumerate(text[:14]):
        product = _GSTIN_ALPHABET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    if _GSTIN_ALPHABET[(36 - total % 36) % 36] != text[14]:
        return "check digit is wrong"
    return None


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------
def validate_and_normalise(
    extracted: ExtractedInvoice, rules: Rules
) -> tuple[NormalisedInvoice, list[Finding]]:
    """Normalise extracted fields and report everything that does not add up."""
    findings: list[Finding] = []
    inv = NormalisedInvoice(raw=extracted)

    inv.vendor_name = (extracted.vendor_name or "").strip() or None
    inv.vendor_gstin = (extracted.vendor_gstin or "").strip().upper().replace(" ", "") or None
    inv.bill_to_name = (extracted.bill_to_name or "").strip() or None
    inv.bill_to_gstin = (extracted.bill_to_gstin or "").strip().upper().replace(" ", "") or None
    inv.invoice_number = (extracted.invoice_number or "").strip() or None
    inv.invoice_number_normalised = normalise_invoice_number(inv.invoice_number)
    inv.invoice_date = parse_date(extracted.invoice_date)
    inv.due_date = parse_date(extracted.due_date)
    inv.po_reference = (extracted.po_reference or "").strip() or None
    inv.po_reference_normalised = normalise_po_reference(inv.po_reference)
    inv.currency = (extracted.currency or "INR").strip().upper()
    inv.lines = list(extracted.lines)
    inv.bank_account_last4 = (extracted.bank_account_last4 or "").strip() or None
    inv.tax_inclusive = bool(extracted.tax_inclusive)
    inv.tax_rate_pct = extracted.tax_rate_pct
    inv.subtotal = extracted.subtotal
    inv.tax_total = extracted.tax_total
    inv.total = extracted.total

    # --- missing critical fields ------------------------------------------
    if not inv.invoice_number:
        findings.append(Finding("MISSING_INVOICE_NUMBER", STAGE))
    if not inv.invoice_date:
        findings.append(Finding("MISSING_INVOICE_DATE", STAGE,
                                {"raw": extracted.invoice_date}))
    if inv.total is None:
        findings.append(Finding("MISSING_TOTAL", STAGE))

    # --- GSTIN format ------------------------------------------------------
    problem = gstin_problem(inv.vendor_gstin)
    if problem and rules.boolean("vendor.validate_gstin_checksum"):
        findings.append(Finding("GSTIN_INVALID_FORMAT", STAGE,
                                {"gstin": inv.vendor_gstin, "problem": problem}))

    # --- tax-inclusive presentation ---------------------------------------
    # When rates include GST, the stated "total" is the gross and the taxable
    # value must be back-computed before anything is compared with a PO, whose
    # line prices are pre-tax.
    rate = inv.tax_rate_pct or rules.decimal("tax.standard_gst_rate_pct")
    if inv.tax_inclusive and inv.total is not None:
        divisor = 1 + rate / 100
        taxable = (inv.total / divisor).quantize(CENT)
        inv.subtotal = taxable
        inv.tax_total = (inv.total - taxable).quantize(CENT)

        # The line prices must come down to pre-tax too. PO line prices are
        # held pre-tax, so comparing a GST-inclusive rate against them would
        # manufacture a price variance of exactly the GST rate on every line.
        # The document's own figures stay available on inv.raw for display.
        inv.lines = [
            replace(
                line,
                unit_price=(line.unit_price / divisor).quantize(CENT)
                if line.unit_price is not None else None,
                amount=(line.amount / divisor).quantize(CENT)
                if line.amount is not None else None,
            )
            for line in inv.lines
        ]

        findings.append(Finding("TAX_INCLUSIVE_PRESENTATION", STAGE, {
            "subtotal": format_inr(inv.subtotal), "tax": format_inr(inv.tax_total),
            "rate": f"{rate.normalize():f}",
        }))

    # --- arithmetic: lines sum to subtotal ---------------------------------
    rounding = rules.rounding_abs
    line_amounts = [l.amount for l in inv.lines if l.amount is not None]
    line_sum = sum(line_amounts, Decimal("0")).quantize(CENT) if line_amounts else None

    if line_sum is not None and inv.subtotal is not None and not inv.tax_inclusive:
        difference = (line_sum - inv.subtotal).quantize(CENT)
        if abs(difference) > rounding:
            findings.append(Finding("ARITHMETIC_LINES_VS_SUBTOTAL", STAGE, {
                "line_sum": format_inr(line_sum), "subtotal": format_inr(inv.subtotal),
                "difference": format_inr(abs(difference)),
            }))

    # --- arithmetic: subtotal + tax = total --------------------------------
    if inv.subtotal is not None and inv.tax_total is not None and inv.total is not None:
        computed = (inv.subtotal + inv.tax_total).quantize(CENT)
        difference = (computed - inv.total).quantize(CENT)
        if abs(difference) > rounding:
            findings.append(Finding("ARITHMETIC_SUBTOTAL_TAX_TOTAL", STAGE, {
                "subtotal": format_inr(inv.subtotal), "tax": format_inr(inv.tax_total),
                "computed": format_inr(computed), "total": format_inr(inv.total),
                "difference": format_inr(abs(difference)),
            }))

    # --- confidence --------------------------------------------------------
    inv.confidence = _derive_confidence(inv, extracted, findings)
    threshold = rules.number("extraction.min_field_confidence")
    critical = rules.strings("extraction.critical_fields")
    low = {f: inv.confidence[f] for f in critical
           if f in inv.confidence and inv.confidence[f] < threshold}
    # A field that is absent is already reported as missing; reporting it a
    # second time as low-confidence would double-count the same problem.
    low = {f: s for f, s in low.items() if _field_present(inv, f)}
    if low:
        detail = ", ".join(f"{f} ({s:.0%})" for f, s in sorted(low.items()))
        findings.append(Finding("LOW_CONFIDENCE_CRITICAL_FIELD", STAGE, {
            "detail": detail, "threshold": threshold, "fields": list(low),
        }))

    return inv, findings


def _field_present(inv: NormalisedInvoice, field_name: str) -> bool:
    return getattr(inv, field_name, None) is not None


def _derive_confidence(
    inv: NormalisedInvoice, extracted: ExtractedInvoice, findings: list[Finding]
) -> dict[str, float]:
    """Combine the model's self-reported confidence with what we can verify.

    Self-reported confidence is not trustworthy on its own - a model will
    cheerfully report 0.95 on a number it misread. Where an independent check
    exists (the arithmetic ties out, a date parsed, a GSTIN checksum passed),
    it caps the score, so the confidence shown in the UI reflects evidence and
    not just the model's mood.
    """
    reported = {k: float(v) for k, v in (extracted.field_confidence or {}).items()}
    codes = {f.code for f in findings}
    derived: dict[str, float] = {}

    arithmetic_broken = bool(codes & {"ARITHMETIC_LINES_VS_SUBTOTAL",
                                      "ARITHMETIC_SUBTOTAL_TAX_TOTAL"})

    for name in ("vendor_name", "vendor_gstin", "invoice_number", "invoice_date",
                 "due_date", "po_reference", "subtotal", "tax_total", "total"):
        value = getattr(inv, name, None)
        model_score = reported.get(name, 0.5 if value is not None else 0.0)
        if value is None:
            derived[name] = 0.0
            continue
        cap = 1.0
        if name == "invoice_date" and extracted.invoice_date and inv.invoice_date is None:
            cap = 0.3                       # model returned text we could not parse
        if name == "vendor_gstin" and gstin_problem(inv.vendor_gstin):
            cap = 0.4                       # failed an objective format check
        if name in ("subtotal", "tax_total", "total") and arithmetic_broken:
            cap = 0.6                       # the numbers do not tie out
        derived[name] = round(min(model_score, cap), 3)
    return derived


