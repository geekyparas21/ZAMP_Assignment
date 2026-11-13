"""
Generate realistic sample invoice PDFs into sample_invoices/.

Three genuinely different vendor layouts are implemented, because the whole
point of using a model for extraction is that field positions and labels vary:

  Template A "classic"  - bordered table, labels "Invoice No." / "P.O. No.",
                          single IGST line, totals block bottom-right.
  Template B "modern"   - coloured header band, labels "Bill #" / "Ref:",
                          ruled-rows table, tax split into CGST 9% + SGST 9%.
  Template C "compact"  - dense right-aligned header, labels "Ref" / "Dated",
                          summary block, optionally GST-INCLUSIVE rates.

One sample is additionally flattened into an image-only PDF (rotated, blurred,
noisy, grey paper) to exercise the vision extraction path. The script verifies
in code that the result has no extractable text layer.

    python scripts/generate_invoices.py
"""

from __future__ import annotations

import io
import json
import math
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pymupdf
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "sample_invoices"

BUYER = {
    "name": "Acme Manufacturing Pvt Ltd",
    "address": "Plot 42, MIDC Industrial Area, Chakan\nPune 410501, Maharashtra",
    "gstin": "27AAACA1111A1Z8",
}

TWO = Decimal("0.01")


def inr(amount: Decimal) -> str:
    """Format a Decimal in Indian digit grouping, e.g. 1,07,026.00"""
    q = amount.quantize(TWO)
    sign = "-" if q < 0 else ""
    whole, _, frac = f"{abs(q):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{sign}{whole}.{frac}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty",
         "Ninety"]


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def amount_in_words(amount: Decimal) -> str:
    """Render an amount the way an Indian invoice does: lakh/crore, then paise."""
    q = amount.quantize(TWO)
    rupees = int(q)
    paise = int((q - rupees) * 100)
    if rupees == 0:
        words = "Zero"
    else:
        parts = []
        for divisor, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
            if rupees >= divisor:
                parts.append(f"{_under_hundred(rupees // divisor)} {label}")
                rupees %= divisor
        if rupees >= 100:
            parts.append(f"{_ONES[rupees // 100]} Hundred")
            rupees %= 100
        if rupees:
            parts.append(_under_hundred(rupees))
        words = " ".join(parts)
    out = f"Rupees {words}"
    if paise:
        out += f" and {_under_hundred(paise)} Paise"
    return out + " Only"


@dataclass
class Line:
    description: str
    hsn: str
    qty: Decimal
    unit: str
    unit_price: Decimal

    @property
    def amount(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(TWO)

    def qty_str(self) -> str:
        return f"{self.qty.normalize():f}" if self.qty % 1 else f"{int(self.qty)}"


@dataclass
class Invoice:
    filename: str
    scenario: str
    template: str
    vendor_name: str
    vendor_address: str
    vendor_gstin: str
    vendor_bank_last4: str
    invoice_number: str | None
    invoice_date: str
    due_date: str
    po_reference: str | None
    lines: list[Line]
    gst_rate: Decimal = Decimal("18")
    tax_inclusive: bool = False
    extra_note: str = ""
    doc_title: str = "TAX INVOICE"
    scanned: bool = False
    bank_last4_override: str | None = None
    freight: Decimal | None = None

    # ---- arithmetic -----------------------------------------------------
    @property
    def line_total(self) -> Decimal:
        return sum((l.amount for l in self.lines), Decimal("0")).quantize(TWO)

    @property
    def subtotal(self) -> Decimal:
        """Taxable value. For tax-inclusive invoices this is back-computed."""
        gross = self.line_total + (self.freight or Decimal("0"))
        if self.tax_inclusive:
            return (gross / (1 + self.gst_rate / 100)).quantize(TWO)
        return gross.quantize(TWO)

    @property
    def tax(self) -> Decimal:
        if self.tax_inclusive:
            return (self.grand_total - self.subtotal).quantize(TWO)
        return (self.subtotal * self.gst_rate / 100).quantize(TWO)

    @property
    def grand_total(self) -> Decimal:
        gross = self.line_total + (self.freight or Decimal("0"))
        if self.tax_inclusive:
            return gross.quantize(TWO)
        return (gross + (gross * self.gst_rate / 100).quantize(TWO)).quantize(TWO)

    @property
    def bank_last4(self) -> str:
        return self.bank_last4_override or self.vendor_bank_last4


# ===========================================================================
# Template A — "classic"
# ===========================================================================
def render_template_a(c: rl_canvas.Canvas, inv: Invoice) -> None:
    W, H = A4
    y = H - 20 * mm

    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, y, inv.vendor_name)
    c.setFont("Helvetica", 8.5)
    for i, ln in enumerate(inv.vendor_address.split("\n")):
        c.drawString(20 * mm, y - 6 * mm - i * 4 * mm, ln)
    c.drawString(20 * mm, y - 6 * mm - len(inv.vendor_address.split("\n")) * 4 * mm,
                 f"GSTIN: {inv.vendor_gstin}")

    c.setFont("Helvetica-Bold", 17)
    c.drawRightString(W - 20 * mm, y, inv.doc_title)
    c.setLineWidth(1.1)
    c.line(20 * mm, y - 24 * mm, W - 20 * mm, y - 24 * mm)

    # Meta block, right aligned, label : value
    my = y - 32 * mm
    meta = [("Invoice No.", inv.invoice_number or ""),
            ("Invoice Date", inv.invoice_date),
            ("Due Date", inv.due_date)]
    if inv.po_reference:
        meta.append(("P.O. No.", inv.po_reference))
    c.setFont("Helvetica", 9)
    for label, value in meta:
        c.drawString(W - 78 * mm, my, f"{label}:")
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(W - 20 * mm, my, value)
        c.setFont("Helvetica", 9)
        my -= 5.2 * mm

    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y - 32 * mm, "Billed To")
    c.setFont("Helvetica", 8.5)
    c.drawString(20 * mm, y - 37 * mm, BUYER["name"])
    for i, ln in enumerate(BUYER["address"].split("\n")):
        c.drawString(20 * mm, y - 41.5 * mm - i * 4 * mm, ln)
    c.drawString(20 * mm, y - 41.5 * mm - 2 * 4 * mm, f"GSTIN: {BUYER['gstin']}")

    # Bordered line table
    ty = my - 10 * mm
    cols = [20 * mm, 30 * mm, 100 * mm, 122 * mm, 142 * mm, 168 * mm, W - 20 * mm]
    headers = ["#", "Description", "HSN", "Qty", "Rate", "Amount"]
    c.setFillColor(colors.HexColor("#e8e8e8"))
    c.rect(cols[0], ty - 7 * mm, cols[-1] - cols[0], 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    for i, h in enumerate(headers):
        if i >= 3:
            c.drawRightString(cols[i + 1] - 2 * mm, ty - 5 * mm, h)
        else:
            c.drawString(cols[i] + 2 * mm, ty - 5 * mm, h)

    c.setFont("Helvetica", 8.5)
    ry = ty - 7 * mm
    for idx, line in enumerate(inv.lines, start=1):
        ry -= 7 * mm
        c.drawString(cols[0] + 2 * mm, ry + 2 * mm, str(idx))
        c.drawString(cols[1] + 2 * mm, ry + 2 * mm, line.description[:52])
        c.drawString(cols[2] + 2 * mm, ry + 2 * mm, line.hsn)
        c.drawRightString(cols[4] - 2 * mm, ry + 2 * mm, f"{line.qty_str()} {line.unit}")
        c.drawRightString(cols[5] - 2 * mm, ry + 2 * mm, inr(line.unit_price))
        c.drawRightString(cols[6] - 2 * mm, ry + 2 * mm, inr(line.amount))
    if inv.freight:
        ry -= 7 * mm
        c.drawString(cols[1] + 2 * mm, ry + 2 * mm, "Freight & handling charges")
        c.drawRightString(cols[6] - 2 * mm, ry + 2 * mm, inr(inv.freight))

    c.setLineWidth(0.5)
    c.rect(cols[0], ry, cols[-1] - cols[0], ty - ry, fill=0, stroke=1)
    for x in cols[1:-1]:
        c.line(x, ry, x, ty)

    # Totals
    sy = ry - 9 * mm
    rows = [("Taxable Value", inv.subtotal),
            (f"IGST @ {inv.gst_rate.normalize():f}%", inv.tax)]
    c.setFont("Helvetica", 9)
    for label, value in rows:
        c.drawRightString(cols[5] - 2 * mm, sy, label)
        c.drawRightString(cols[6] - 2 * mm, sy, inr(value))
        sy -= 5.5 * mm
    c.setLineWidth(0.8)
    c.line(cols[4], sy + 2 * mm, cols[6], sy + 2 * mm)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawRightString(cols[4] - 2 * mm, sy - 4 * mm, "Grand Total")
    c.drawRightString(cols[6] - 2 * mm, sy - 4 * mm, f"INR {inr(inv.grand_total)}")

    c.setFont("Helvetica-Bold", 8)
    c.drawString(20 * mm, sy - 14 * mm, "Amount in words:")
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, sy - 18.5 * mm, amount_in_words(inv.grand_total))

    _footer(c, inv, sy - 30 * mm)


# ===========================================================================
# Template B — "modern"
# ===========================================================================
def render_template_b(c: rl_canvas.Canvas, inv: Invoice) -> None:
    W, H = A4
    band = colors.HexColor("#1f3b57")
    c.setFillColor(band)
    c.rect(0, H - 34 * mm, W, 34 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18 * mm, H - 16 * mm, inv.vendor_name)
    c.setFont("Helvetica", 8)
    c.drawString(18 * mm, H - 21.5 * mm, inv.vendor_address.replace("\n", "  |  "))
    c.drawString(18 * mm, H - 26 * mm, f"GSTIN {inv.vendor_gstin}")
    c.setFont("Helvetica-Bold", 19)
    c.drawRightString(W - 18 * mm, H - 18 * mm, inv.doc_title.title())
    c.setFillColor(colors.black)

    y = H - 46 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#777777"))
    labels = [("BILL #", inv.invoice_number or "—"), ("BILL DATE", inv.invoice_date),
              ("PAYMENT DUE", inv.due_date)]
    if inv.po_reference:
        labels.append(("REF", inv.po_reference))
    xs = [18 * mm, 58 * mm, 98 * mm, 138 * mm]
    for (label, _), x in zip(labels, xs):
        c.drawString(x, y, label)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.5)
    for (_, value), x in zip(labels, xs):
        c.drawString(x, y - 5 * mm, value)

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#777777"))
    c.drawString(18 * mm, y - 15 * mm, "INVOICE TO")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(18 * mm, y - 20 * mm, BUYER["name"])
    c.setFont("Helvetica", 8)
    for i, ln in enumerate(BUYER["address"].split("\n")):
        c.drawString(18 * mm, y - 24.5 * mm - i * 4 * mm, ln)
    c.drawString(18 * mm, y - 24.5 * mm - 8 * mm, f"GSTIN {BUYER['gstin']}")

    # Ruled-row table, no vertical rules
    ty = y - 44 * mm
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(18 * mm, ty, "ITEM / SERVICE")
    c.drawRightString(120 * mm, ty, "QTY")
    c.drawRightString(152 * mm, ty, "UNIT PRICE")
    c.drawRightString(W - 18 * mm, ty, "AMOUNT")
    c.setFillColor(colors.black)
    c.setLineWidth(0.7)
    c.line(18 * mm, ty - 2.5 * mm, W - 18 * mm, ty - 2.5 * mm)

    ry = ty - 2.5 * mm
    for line in inv.lines:
        ry -= 9 * mm
        c.setFont("Helvetica", 9)
        c.drawString(18 * mm, ry + 2.5 * mm, line.description[:58])
        c.setFont("Helvetica", 7)
        c.setFillColor(colors.HexColor("#888888"))
        c.drawString(18 * mm, ry - 0.8 * mm, f"HSN {line.hsn}")
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9)
        c.drawRightString(120 * mm, ry + 2.5 * mm, f"{line.qty_str()} {line.unit}")
        c.drawRightString(152 * mm, ry + 2.5 * mm, inr(line.unit_price))
        c.drawRightString(W - 18 * mm, ry + 2.5 * mm, inr(line.amount))
        c.setStrokeColor(colors.HexColor("#dddddd"))
        c.setLineWidth(0.4)
        c.line(18 * mm, ry - 3 * mm, W - 18 * mm, ry - 3 * mm)
        c.setStrokeColor(colors.black)

    if inv.freight:
        ry -= 9 * mm
        c.setFont("Helvetica", 9)
        c.drawString(18 * mm, ry + 2.5 * mm, "Freight & handling charges")
        c.drawRightString(W - 18 * mm, ry + 2.5 * mm, inr(inv.freight))

    # Tax split CGST/SGST
    half = (inv.tax / 2).quantize(TWO)
    sy = ry - 12 * mm
    rate_half = (inv.gst_rate / 2).normalize()
    rows = [("Subtotal", inv.subtotal),
            (f"CGST {rate_half:f}%", half),
            (f"SGST {rate_half:f}%", inv.tax - half)]
    c.setFont("Helvetica", 9)
    for label, value in rows:
        c.drawRightString(152 * mm, sy, label)
        c.drawRightString(W - 18 * mm, sy, inr(value))
        sy -= 5.5 * mm
    c.setFillColor(colors.HexColor("#1f3b57"))
    c.rect(112 * mm, sy - 9 * mm, W - 18 * mm - 112 * mm, 9 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(115 * mm, sy - 6 * mm, "TOTAL DUE")
    c.drawRightString(W - 20 * mm, sy - 6 * mm, f"INR {inr(inv.grand_total)}")
    c.setFillColor(colors.black)

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#777777"))
    c.drawString(18 * mm, sy - 16 * mm, "AMOUNT IN WORDS")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 8.5)
    c.drawString(18 * mm, sy - 21 * mm, amount_in_words(inv.grand_total))

    _footer(c, inv, sy - 32 * mm)


# ===========================================================================
# Template C — "compact"
# ===========================================================================
def render_template_c(c: rl_canvas.Canvas, inv: Invoice) -> None:
    W, H = A4
    y = H - 18 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(16 * mm, y, inv.vendor_name.upper())
    c.setFont("Helvetica", 7.5)
    c.drawString(16 * mm, y - 4.5 * mm, inv.vendor_address.replace("\n", ", "))
    c.drawString(16 * mm, y - 8.5 * mm, f"GST No. {inv.vendor_gstin}")

    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(W - 16 * mm, y, inv.doc_title)
    c.setFont("Helvetica", 8)
    ry = y - 5 * mm
    meta = []
    if inv.invoice_number:
        meta.append(("Ref", inv.invoice_number))
    meta.append(("Dated", inv.invoice_date))
    meta.append(("Terms", f"Payable by {inv.due_date}"))
    if inv.po_reference:
        meta.append(("Against Order", inv.po_reference))
    for label, value in meta:
        c.drawRightString(W - 16 * mm, ry, f"{label}  :  {value}")
        ry -= 4.2 * mm

    c.setLineWidth(0.6)
    c.line(16 * mm, ry - 2 * mm, W - 16 * mm, ry - 2 * mm)

    c.setFont("Helvetica", 7.5)
    c.drawString(16 * mm, ry - 8 * mm, "To,")
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(16 * mm, ry - 12 * mm, BUYER["name"])
    c.setFont("Helvetica", 7.5)
    c.drawString(16 * mm, ry - 16 * mm, BUYER["address"].replace("\n", ", "))
    c.drawString(16 * mm, ry - 20 * mm, f"GST No. {BUYER['gstin']}")

    ty = ry - 30 * mm
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(16 * mm, ty, "SR")
    c.drawString(24 * mm, ty, "PARTICULARS")
    c.drawRightString(118 * mm, ty, "HSN")
    c.drawRightString(140 * mm, ty, "QTY")
    c.drawRightString(166 * mm, ty, "RATE")
    c.drawRightString(W - 16 * mm, ty, "VALUE")
    c.line(16 * mm, ty - 2 * mm, W - 16 * mm, ty - 2 * mm)

    c.setFont("Helvetica", 8)
    rr = ty - 2 * mm
    for idx, line in enumerate(inv.lines, start=1):
        rr -= 6 * mm
        c.drawString(16 * mm, rr + 1.5 * mm, str(idx))
        c.drawString(24 * mm, rr + 1.5 * mm, line.description[:50])
        c.drawRightString(118 * mm, rr + 1.5 * mm, line.hsn)
        c.drawRightString(140 * mm, rr + 1.5 * mm, f"{line.qty_str()} {line.unit}")
        c.drawRightString(166 * mm, rr + 1.5 * mm, inr(line.unit_price))
        c.drawRightString(W - 16 * mm, rr + 1.5 * mm, inr(line.amount))
    if inv.freight:
        rr -= 6 * mm
        c.drawString(24 * mm, rr + 1.5 * mm, "Freight & handling")
        c.drawRightString(W - 16 * mm, rr + 1.5 * mm, inr(inv.freight))

    c.line(16 * mm, rr - 1 * mm, W - 16 * mm, rr - 1 * mm)

    sy = rr - 8 * mm
    c.setFont("Helvetica", 8)
    if inv.tax_inclusive:
        c.drawString(16 * mm, sy, f"Rates are INCLUSIVE of GST @ {inv.gst_rate.normalize():f}%.")
        c.drawString(16 * mm, sy - 4.5 * mm,
                     f"Taxable value {inr(inv.subtotal)} | GST {inr(inv.tax)}")
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(W - 16 * mm, sy - 2 * mm, f"TOTAL  INR {inr(inv.grand_total)}")
    else:
        rows = [("Taxable value", inv.subtotal),
                (f"Add: GST @ {inv.gst_rate.normalize():f}%", inv.tax)]
        for label, value in rows:
            c.drawRightString(166 * mm, sy, label)
            c.drawRightString(W - 16 * mm, sy, inr(value))
            sy -= 5 * mm
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(166 * mm, sy - 2 * mm, "TOTAL")
        c.drawRightString(W - 16 * mm, sy - 2 * mm, f"INR {inr(inv.grand_total)}")

    c.setFont("Helvetica", 7.5)
    c.drawString(16 * mm, sy - 12 * mm, f"Rupees in words: {amount_in_words(inv.grand_total)}")

    _footer(c, inv, sy - 24 * mm)


def _footer(c: rl_canvas.Canvas, inv: Invoice, y: float) -> None:
    W, _ = A4
    c.setFont("Helvetica-Bold", 8)
    c.drawString(18 * mm, y, "Bank Details")
    c.setFont("Helvetica", 7.5)
    c.drawString(18 * mm, y - 4.5 * mm,
                 f"A/c No. XXXXXXXX{inv.bank_last4}  |  IFSC HDFC0001234  |  HDFC Bank")
    if inv.extra_note:
        c.drawString(18 * mm, y - 10 * mm, inv.extra_note)
    c.setFont("Helvetica-Oblique", 7)
    c.drawRightString(W - 18 * mm, y - 4.5 * mm, f"For {inv.vendor_name}")
    c.drawRightString(W - 18 * mm, y - 14 * mm, "Authorised Signatory")


TEMPLATES = {"A": render_template_a, "B": render_template_b, "C": render_template_c}


def build_pdf(inv: Invoice) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{inv.doc_title} {inv.invoice_number or ''}".strip())
    TEMPLATES[inv.template](c, inv)
    c.showPage()
    c.save()
    return buf.getvalue()


# ===========================================================================
# Scanning simulation
# ===========================================================================
def scanify(pdf_bytes: bytes, seed: int = 7) -> bytes:
    """Flatten a PDF into an image-only PDF that looks like a desk scan.

    Renders at 150 dpi, tints the paper grey, adds sensor noise and a slight
    blur, rotates a fraction of a degree, then writes the image back as a PDF
    page. The result carries no text layer at all.
    """
    rng = random.Random(seed)
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=144)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        # Grey-beige paper, the way a flatbed renders white
        img = Image.blend(img, Image.new("RGB", img.size, (226, 222, 211)), 0.30)

        # Uneven illumination: one corner lit brighter than the rest
        grad = Image.new("L", img.size)
        gd = ImageDraw.Draw(grad)
        for i in range(0, img.size[1], 8):
            gd.rectangle([0, i, img.size[0], i + 8],
                         fill=int(40 * (1 - i / img.size[1])))
        img = Image.composite(Image.new("RGB", img.size, (255, 253, 246)), img, grad)

        # Sensor noise
        noise = Image.effect_noise(img.size, 26).convert("L")
        img = Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.13)

        # Scanner softness
        img = img.filter(ImageFilter.GaussianBlur(0.9))

        # Skew, as if the page was not square on the glass
        angle = rng.uniform(-1.4, -0.8) if rng.random() < 0.5 else rng.uniform(0.8, 1.4)
        img = img.rotate(angle, resample=Image.BICUBIC, expand=False,
                         fillcolor=(214, 210, 200))

        # Dark bands down the edges, typical of a lifted flatbed lid
        shadow = Image.new("L", img.size, 0)
        d = ImageDraw.Draw(shadow)
        d.rectangle([0, 0, int(img.size[0] * 0.018), img.size[1]], fill=110)
        d.rectangle([int(img.size[0] * 0.985), 0, img.size[0], img.size[1]], fill=70)
        d.rectangle([0, 0, img.size[0], int(img.size[1] * 0.008)], fill=80)
        shadow = shadow.filter(ImageFilter.GaussianBlur(6))
        img = Image.composite(Image.new("RGB", img.size, (104, 101, 95)), img, shadow)

        # Speckle: dust on the glass
        sd = ImageDraw.Draw(img)
        for _ in range(140):
            x, y = rng.randrange(img.size[0]), rng.randrange(img.size[1])
            r = rng.choice([0, 0, 1])
            g = rng.randint(90, 160)
            sd.ellipse([x, y, x + r, y + r], fill=(g, g, g))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=58)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=buf.getvalue())
    result = out.tobytes()
    src.close()
    out.close()
    return result


def extractable_chars(pdf_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return sum(len((p.extract_text() or "").strip()) for p in pdf.pages)


# ===========================================================================
# The samples
# ===========================================================================
D = Decimal


def samples() -> list[Invoice]:
    bharat = dict(vendor_name="Bharat Steel Ind.",
                  vendor_address="Gat No. 221, Kurkumbh MIDC\nDaund, Pune 413802",
                  vendor_gstin="27AABCB1234D1ZC", vendor_bank_last4="4412")
    nova = dict(vendor_name="Nova Polymers Pvt Ltd",
                vendor_address="Survey 88/2, Jigani Industrial Area\nBengaluru 560105",
                vendor_gstin="29AACCN5678E1Z5", vendor_bank_last4="7781")
    kaveri = dict(vendor_name="Kaveri Logistics Services",
                  vendor_address="18 Anna Salai, Guindy\nChennai 600032",
                  vendor_gstin="33AADCK9012F1ZW", vendor_bank_last4="2290")
    deccan = dict(vendor_name="Deccan Precision Tools Pvt. Ltd.",
                  vendor_address="Plot 9, Balanagar Industrial Estate\nHyderabad 500037",
                  vendor_gstin="36AAECD3456G1ZQ", vendor_bank_last4="5533")
    sunrise = dict(vendor_name="Sunrise Packaging Co.",
                   vendor_address="C-14, GIDC Vatva\nAhmedabad 382445",
                   vendor_gstin="24AAFCS7890H1ZZ", vendor_bank_last4="9021")
    orient = dict(vendor_name="Orient Electricals Ltd",
                  vendor_address="221 Okhla Phase III\nNew Delhi 110020",
                  vendor_gstin="07AAGCO2345J1ZD", vendor_bank_last4="1187")
    vega = dict(vendor_name="Vega Instruments Pvt Ltd",
                vendor_address="7 Salt Lake Sector V\nKolkata 700091",
                vendor_gstin="19AAICV0123L1Z7", vendor_bank_last4="6655")
    trident = dict(vendor_name="Trident Fasteners & Hardware",
                   vendor_address="44 Industrial Estate, Govindpura\nBhopal 462023",
                   vendor_gstin="23AAJCT4567M1ZT", vendor_bank_last4="8809")
    surya = dict(vendor_name="Suryodaya Engg Works",
                 vendor_address="E-7 Sitapura Industrial Area\nJaipur 302022",
                 vendor_gstin="08AAKCS8901N1ZP", vendor_bank_last4="4470")

    return [
        # --- 0. Happy path -------------------------------------------------
        Invoice(
            filename="00_happy_path_bharat_steel.pdf",
            scenario="Happy path — approved vendor, explicit PO, amounts match the PO exactly.",
            template="A", **bharat,
            invoice_number="BSI-2026-1042", invoice_date="2026-11-04",
            due_date="2026-12-04", po_reference="PO-4501",
            lines=[Line("MS Plate 10mm IS2062 Gr.B", "7208", D("120"), "NOS", D("485.00")),
                   Line("MS Angle 50x50x5mm", "7216", D("200"), "NOS", D("162.50"))],
        ),

        # --- 1. Scanned, no PO reference -----------------------------------
        Invoice(
            filename="01_scanned_no_po_deccan.pdf",
            scenario="Scanned image-only PDF with no PO reference — PO must be inferred.",
            template="C", **deccan,
            invoice_number="DPT/2026/0247", invoice_date="2026-11-05",
            due_date="2026-12-05", po_reference=None,
            lines=[Line("Carbide turning insert CNMG120408", "8209", D("500"), "PCS", D("212.00")),
                   Line("Tool holder PCLNR2525M12", "8466", D("10"), "NOS", D("3450.00"))],
            extra_note="Delivered against verbal confirmation from Stores.",
            scanned=True,
        ),

        # --- 1b. Ambiguous PO inference ------------------------------------
        Invoice(
            filename="01b_ambiguous_po_sunrise.pdf",
            scenario="No PO reference and two near-identical open POs — inference is ambiguous.",
            template="B", **sunrise,
            invoice_number="SPC-2026-0417", invoice_date="2026-11-07",
            due_date="2026-12-07", po_reference=None,
            lines=[Line("Corrugated box 5-ply 600x400x300mm", "4819", D("3020"), "NOS", D("46.00"))],
        ),

        # --- 2. Split PO across three invoices ------------------------------
        Invoice(
            filename="02a_split_po_nova_1of3.pdf",
            scenario="Split PO 1/3 — part-bills PO-4510. Approves and moves the PO balance.",
            template="B", **nova,
            invoice_number="NPL-2026-0712", invoice_date="2026-11-02",
            due_date="2026-12-17", po_reference="PO-4510",
            lines=[Line("Polypropylene granules PP-H110", "3902", D("2000"), "KG", D("85.00"))],
        ),
        Invoice(
            filename="02b_split_po_nova_2of3.pdf",
            scenario="Split PO 2/3 — second part-bill. Remaining balance drops to ~1,20,360.",
            template="B", **nova,
            invoice_number="NPL-2026-0736", invoice_date="2026-11-08",
            due_date="2026-12-23", po_reference="PO-4510",
            # Deliberately a different quantity from invoice 1. Two part-bills
            # of the IDENTICAL amount within the 7-day window would (correctly)
            # trip the suspected-duplicate rule, which would mask the
            # over-billing this scenario exists to demonstrate.
            lines=[Line("Polypropylene granules PP-H110", "3902", D("1800"), "KG", D("85.00"))],
        ),
        Invoice(
            filename="02c_split_po_nova_3of3_overbilled.pdf",
            scenario="Split PO 3/3 — exceeds the remaining PO balance. Must be held, not paid.",
            template="B", **nova,
            invoice_number="NPL-2026-0759", invoice_date="2026-11-12",
            due_date="2026-12-27", po_reference="PO-4510",
            lines=[Line("Polypropylene granules PP-H110", "3902", D("1500"), "KG", D("85.00"))],
        ),

        # --- 3. Disguised duplicate ----------------------------------------
        Invoice(
            filename="03_duplicate_vega.pdf",
            scenario="Re-send of an already-paid invoice with the number reformatted "
                     "(INV/2026/42 vs INV-2026-0042).",
            template="A", **vega,
            invoice_number="INV/2026/42", invoice_date="2026-10-15",
            due_date="2026-11-14", po_reference="PO-4540",
            lines=[Line("Digital pressure gauge 0-100 bar", "9026", D("40"), "NOS", D("2000.00"))],
        ),

        # --- 3b. Suspected duplicate (and tax-inclusive presentation) -------
        Invoice(
            filename="03b_suspected_duplicate_suryodaya.pdf",
            scenario="Different invoice number, same vendor and amount within 7 days. "
                     "Rates are quoted GST-inclusive.",
            template="C", **surya,
            invoice_number="SEW-2026-131", invoice_date="2026-11-12",
            due_date="2026-12-27", po_reference="PO-4550",
            lines=[Line("Fabricated bracket assembly type-7", "7308", D("150"), "NOS", D("483.80"))],
            tax_inclusive=True,
            extra_note="All rates quoted inclusive of applicable GST.",
        ),

        # --- 4a. Within-tolerance variance ---------------------------------
        Invoice(
            filename="04a_within_tolerance_trident.pdf",
            scenario="Freight of Rs 180 pushes the total above the PO — but inside tolerance.",
            template="A", **trident,
            invoice_number="TFH-2026-2288", invoice_date="2026-11-09",
            due_date="2026-12-09", po_reference="PO-4530",
            lines=[Line("Hex bolt M12x50 Gr.8.8 zinc plated", "7318", D("10000"), "NOS", D("11.50"))],
            freight=D("152.54"),
        ),

        # --- 4b. Real price creep ------------------------------------------
        Invoice(
            filename="04b_price_creep_kaveri.pdf",
            scenario="Unit rate raised 8% above the PO rate — outside tolerance, must be held.",
            template="B", **kaveri,
            invoice_number="KLS-2026-0588", invoice_date="2026-11-10",
            due_date="2026-11-25", po_reference="PO-4531",
            lines=[Line("Road freight Pune to Chennai, per trip", "9965", D("20"), "TRIP",
                        D("9180.00"))],
        ),

        # --- Robustness samples --------------------------------------------
        Invoice(
            filename="05_blocked_vendor_orient.pdf",
            scenario="Vendor is blocked on the vendor master — must be rejected outright.",
            template="A", **orient,
            invoice_number="OEL-2026-0901", invoice_date="2026-11-06",
            due_date="2026-12-06", po_reference="PO-4570",
            lines=[Line("4-core armoured cable 25sqmm", "8544", D("800"), "MTR", D("412.00"))],
        ),
        Invoice(
            filename="06_quotation_not_invoice.pdf",
            scenario="Not an invoice at all — a quotation. Must be detected and rejected.",
            template="C", **deccan,
            invoice_number="QTN-2026-0088", invoice_date="2026-11-11",
            due_date="2026-12-11", po_reference=None,
            lines=[Line("Carbide turning insert CNMG120408", "8209", D("500"), "PCS", D("209.00"))],
            doc_title="QUOTATION",
            extra_note="This is a quotation and is valid for 30 days. Not a demand for payment.",
        ),
        Invoice(
            filename="07_missing_invoice_number_bharat.pdf",
            scenario="No invoice number anywhere on the document — a critical field is missing.",
            template="A", **bharat,
            invoice_number=None, invoice_date="2026-11-07",
            due_date="2026-12-07", po_reference="PO-4590",
            lines=[Line("MS Round bar 20mm", "7214", D("500"), "NOS", D("92.00"))],
        ),
        Invoice(
            filename="08_bank_mismatch_bharat.pdf",
            scenario="Bank account differs from the vendor master — payment-redirection signal.",
            template="A", **bharat,
            invoice_number="BSI-2026-1077", invoice_date="2026-11-11",
            due_date="2026-12-11", po_reference="PO-4590",
            lines=[Line("MS Round bar 20mm", "7214", D("500"), "NOS", D("92.00"))],
            bank_last4_override="9931",
            extra_note="NOTE: Please update our bank account for all future payments.",
        ),
    ]


# The outcome each sample is designed to produce, and the reason codes that
# must appear. The pipeline is never told any of this - it is the assertion
# set for the end-to-end tests and the labels for the UI sample gallery.
EXPECTED: dict[str, tuple[str, tuple[str, ...]]] = {
    "00_happy_path_bharat_steel.pdf": ("APPROVED", ()),
    "01_scanned_no_po_deccan.pdf": ("APPROVED", ("SCANNED_DOCUMENT", "PO_INFERRED_HIGH_CONFIDENCE")),
    "01b_ambiguous_po_sunrise.pdf": ("NEEDS_REVIEW", ("PO_INFERRED_AMBIGUOUS",)),
    "02a_split_po_nova_1of3.pdf": ("APPROVED", ("PARTIAL_INVOICE_AGAINST_PO",)),
    "02b_split_po_nova_2of3.pdf": ("APPROVED", ("PARTIAL_INVOICE_AGAINST_PO",)),
    "02c_split_po_nova_3of3_overbilled.pdf": ("NEEDS_REVIEW", ("PO_OVERBILLED",)),
    "03_duplicate_vega.pdf": ("REJECTED", ("DUPLICATE_CONFIRMED_NUMBER",)),
    "03b_suspected_duplicate_suryodaya.pdf": ("NEEDS_REVIEW",
                                              ("DUPLICATE_SUSPECTED", "TAX_INCLUSIVE_PRESENTATION")),
    "04a_within_tolerance_trident.pdf": ("APPROVED", ("TOTAL_VARIANCE_WITHIN_TOLERANCE",)),
    "04b_price_creep_kaveri.pdf": ("NEEDS_REVIEW", ("PRICE_VARIANCE_EXCEEDS_TOLERANCE",)),
    "05_blocked_vendor_orient.pdf": ("REJECTED", ("VENDOR_BLOCKED",)),
    "06_quotation_not_invoice.pdf": ("REJECTED", ("NOT_AN_INVOICE",)),
    "07_missing_invoice_number_bharat.pdf": ("NEEDS_REVIEW", ("MISSING_INVOICE_NUMBER",)),
    "08_bank_mismatch_bharat.pdf": ("NEEDS_REVIEW", ("VENDOR_BANK_MISMATCH",)),
}


def cross_check_against_pos() -> list[str]:
    """Confirm each sample's arithmetic lines up with the PO it is aimed at.

    Catches the failure mode where a sample is regenerated with new numbers and
    silently stops exercising the scenario it was built for.
    """
    import csv as _csv

    pos = {}
    with (ROOT / "data" / "purchase_orders.csv").open(encoding="utf-8") as fh:
        for row in _csv.DictReader(fh):
            pos[row["po_number"]] = row

    checks = [
        ("00_happy_path_bharat_steel.pdf", "PO-4501",
         lambda t, po: t == po, "invoice total equals PO total"),
        ("02c_split_po_nova_3of3_overbilled.pdf", "PO-4510",
         lambda t, po: t > (po - Decimal("381140.00")) + Decimal("500"),
         "exceeds remaining balance beyond tolerance"),
        ("04a_within_tolerance_trident.pdf", "PO-4530",
         lambda t, po: Decimal("0") < t - po <= Decimal("500"),
         "over PO but inside the Rs 500 band"),
        ("04b_price_creep_kaveri.pdf", "PO-4531",
         lambda t, po: t - po > Decimal("500"), "over PO beyond tolerance"),
        ("01_scanned_no_po_deccan.pdf", "PO-4520",
         lambda t, po: t == po, "matches the PO it must be inferred to"),
    ]
    totals = {inv.filename: inv.grand_total for inv in samples()}
    problems = []
    for filename, po_number, predicate, label in checks:
        total = totals[filename]
        po_total = Decimal(pos[po_number]["total_amount"])
        ok = predicate(total, po_total)
        status = "ok " if ok else "FAIL"
        print(f"  [{status}] {filename:<40} vs {po_number} "
              f"({inr(total)} vs {inr(po_total)}) - {label}")
        if not ok:
            problems.append(f"{filename} vs {po_number}: {label}")
    return problems


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.pdf"):
        old.unlink()

    manifest_rows = []
    failures = []

    for inv in samples():
        pdf = build_pdf(inv)
        kind = "text"
        if inv.scanned:
            pdf = scanify(pdf)
            kind = "scanned"
        path = OUT_DIR / inv.filename
        path.write_bytes(pdf)

        chars = extractable_chars(pdf)
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            pages = len(doc.pages)

        # Verify the scanned sample really has no text layer, and that the
        # text samples really do.
        if inv.scanned and chars > 0:
            failures.append(f"{inv.filename}: expected NO text layer, found {chars} chars")
        if not inv.scanned and chars < 200:
            failures.append(f"{inv.filename}: text layer too thin ({chars} chars)")

        outcome, reasons = EXPECTED.get(inv.filename, ("APPROVED", ()))
        manifest_rows.append({
            "file": inv.filename, "kind": kind, "template": inv.template,
            "vendor": inv.vendor_name, "number": inv.invoice_number or "(missing)",
            "po": inv.po_reference or "(none)", "total": inr(inv.grand_total),
            "total_raw": str(inv.grand_total), "chars": chars, "pages": pages,
            "size_kb": round(len(pdf) / 1024, 1), "scenario": inv.scenario,
            "expected_outcome": outcome, "expected_reasons": list(reasons),
        })

    width = max(len(r["file"]) for r in manifest_rows)
    print(f"Generated {len(manifest_rows)} sample invoices in {OUT_DIR}\n")
    print(f"{'file'.ljust(width)}  {'kind':<8}{'tpl':<5}{'chars':>7}{'KB':>8}  "
          f"{'total':>14}  po")
    print("-" * (width + 60))
    for r in manifest_rows:
        print(f"{r['file'].ljust(width)}  {r['kind']:<8}{r['template']:<5}"
              f"{r['chars']:>7}{r['size_kb']:>8}  {r['total']:>14}  {r['po']}")

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest_rows, indent=2), encoding="utf-8")

    print("\nCross-check against purchase_orders.csv:")
    failures.extend(cross_check_against_pos())

    print()
    if failures:
        print("VERIFICATION FAILED:")
        for f in failures:
            print(f"  ! {f}")
        raise SystemExit(1)
    print("Text-layer verification passed: "
          "the scanned sample has 0 extractable characters; "
          "every text sample has a full text layer.")
    print("Arithmetic cross-check passed: every scenario sample still lines up "
          "with the PO it was built to exercise.")
    print(f"Wrote {OUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
