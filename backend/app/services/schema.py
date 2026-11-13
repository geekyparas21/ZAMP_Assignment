"""The extraction contract.

A strict schema is the boundary between an untrusted model and a system that
decides where money goes. Two jobs:

  1. It is handed to Gemini as a response schema, so the model is constrained
     to this shape rather than asked nicely for JSON.
  2. It validates whatever comes back anyway, because a constrained decode is
     a strong guarantee about structure and no guarantee at all about sense.

Every field is optional by design. A field that is not on the document must
come back null and be flagged downstream - never guessed at, never inferred.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..domain import ExtractedInvoice, ExtractedLine


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce whatever the model emitted for a number into a Decimal.

    Models return amounts as numbers, as strings, with currency symbols, and
    with thousands separators, sometimes varying between fields of the same
    response. Anything that is not actually a number becomes None rather than
    a zero, because a silent zero in an invoice total is a payment bug.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip()
    if not text:
        return None
    for token in ("INR", "Rs.", "Rs", "₹", ",", "/-"):
        text = text.replace(token, "")
    text = text.strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


class LineItemSchema(BaseModel):
    description: str | None = Field(
        None, description="The item or service exactly as written on the invoice.")
    hsn: str | None = Field(
        None, description="HSN or SAC code if one is shown, otherwise null.")
    qty: str | None = Field(
        None, description="Quantity as a plain number, e.g. '120'. Null if absent.")
    unit: str | None = Field(
        None, description="Unit of measure as written, e.g. NOS, KG, MTR, TRIP.")
    unit_price: str | None = Field(
        None, description="Rate per unit as a plain number, no currency symbol.")
    amount: str | None = Field(
        None, description="Line total as a plain number, no currency symbol.")

    def to_domain(self) -> ExtractedLine:
        return ExtractedLine(
            description=(self.description or None),
            hsn=(self.hsn or None),
            qty=_to_decimal(self.qty),
            unit=(self.unit or None),
            unit_price=_to_decimal(self.unit_price),
            amount=_to_decimal(self.amount),
        )


class FieldConfidenceSchema(BaseModel):
    """How sure the model is it read each field correctly, 0.0 to 1.0."""

    vendor_name: float | None = None
    vendor_gstin: float | None = None
    invoice_number: float | None = None
    invoice_date: float | None = None
    due_date: float | None = None
    po_reference: float | None = None
    subtotal: float | None = None
    tax_total: float | None = None
    total: float | None = None


class InvoiceExtractionSchema(BaseModel):
    """What the model must return for any document it is shown."""

    is_invoice: bool = Field(
        True,
        description="False if this document is not a payable invoice - for "
                    "example a quotation, purchase order, receipt, delivery "
                    "note, credit note or statement.")
    document_type: str | None = Field(
        None, description="What the document actually is: invoice, quotation, "
                          "receipt, credit_note, delivery_note, statement or other.")

    vendor_name: str | None = Field(
        None, description="The SUPPLIER's name - whoever is being paid, not the "
                          "'Billed To' / 'Invoice To' party.")
    vendor_gstin: str | None = Field(
        None, description="The supplier's 15-character GSTIN. Not the buyer's.")
    vendor_address: str | None = None

    bill_to_name: str | None = Field(
        None, description="The BUYER's name - the 'Billed To' / 'Invoice To' / "
                          "'To,' party the invoice is addressed to. On a "
                          "consumer receipt this is an individual's name. Null "
                          "if the document names no buyer.")
    bill_to_gstin: str | None = Field(
        None, description="The buyer's 15-character GSTIN, if the document "
                          "shows one. Not the supplier's.")

    invoice_number: str | None = Field(
        None, description="The invoice/bill/reference number exactly as printed, "
                          "keeping its punctuation. Null if the document has none.")
    invoice_date: str | None = Field(
        None, description="Invoice date, normalised to YYYY-MM-DD.")
    due_date: str | None = Field(
        None, description="Payment due date as YYYY-MM-DD, if stated.")
    po_reference: str | None = Field(
        None, description="Purchase order reference exactly as written, e.g. "
                          "'PO-4501'. Null if the document does not mention one. "
                          "Never invent or infer a PO number.")

    currency: str | None = Field(None, description="ISO code, e.g. INR.")
    line_items: list[LineItemSchema] = Field(default_factory=list)

    subtotal: str | None = Field(
        None, description="Taxable value before tax, as a plain number.")
    tax_total: str | None = Field(
        None, description="Total tax. Sum CGST and SGST together if shown separately.")
    tax_rate_pct: str | None = Field(
        None, description="GST percentage applied, e.g. '18'.")
    total: str | None = Field(
        None, description="The grand total payable, as a plain number.")
    tax_inclusive: bool = Field(
        False,
        description="True only if the document states the rates already include "
                    "GST (e.g. 'rates inclusive of GST').")

    bank_account_last4: str | None = Field(
        None, description="Last 4 digits of the bank account number if shown.")
    notes: str | None = Field(
        None, description="Anything unusual worth a human knowing, one sentence.")

    field_confidence: FieldConfidenceSchema = Field(
        default_factory=FieldConfidenceSchema,
        description="Your confidence per field, 0.0-1.0. Be honest: use a low "
                    "value when the text was unclear or you had to guess.")

    @field_validator("vendor_gstin", "bill_to_gstin")
    @classmethod
    def _tidy_gstin(cls, value: str | None) -> str | None:
        return value.strip().upper().replace(" ", "") if value else None

    @field_validator("bank_account_last4")
    @classmethod
    def _last_four_digits(cls, value: str | None) -> str | None:
        """Models sometimes return the whole account number, or a masked form."""
        if not value:
            return None
        digits = "".join(c for c in value if c.isdigit())
        return digits[-4:] if len(digits) >= 4 else None

    def to_domain(self) -> ExtractedInvoice:
        confidence = {
            key: float(value)
            for key, value in self.field_confidence.model_dump().items()
            if value is not None
        }
        return ExtractedInvoice(
            is_invoice=self.is_invoice,
            document_type=(self.document_type or "invoice"),
            vendor_name=(self.vendor_name or None),
            vendor_gstin=self.vendor_gstin,
            bill_to_name=(self.bill_to_name or None),
            bill_to_gstin=self.bill_to_gstin,
            invoice_number=(self.invoice_number or None),
            invoice_date=(self.invoice_date or None),
            due_date=(self.due_date or None),
            po_reference=(self.po_reference or None),
            currency=(self.currency or None),
            lines=[item.to_domain() for item in self.line_items],
            subtotal=_to_decimal(self.subtotal),
            tax_total=_to_decimal(self.tax_total),
            total=_to_decimal(self.total),
            tax_rate_pct=_to_decimal(self.tax_rate_pct),
            tax_inclusive=bool(self.tax_inclusive),
            bank_account_last4=self.bank_account_last4,
            field_confidence=confidence,
            notes=(self.notes or None),
        )


#: Sent with every request. It tells the model what it is reading and, more
#: importantly, what it must NOT do: invent a PO, guess a missing field, or
#: form an opinion about whether the invoice should be paid.
EXTRACTION_PROMPT = """\
You are reading a supplier invoice for an accounts-payable system at an Indian
manufacturing company. Extract the fields exactly as they appear on the
document.

Rules you must follow:

1. Extract only what is printed on the document. If a field is not there,
   return null for it. Never guess, never infer, never fill a gap with a
   plausible value.
2. The vendor is the SUPPLIER being paid - the company whose name is at the
   top and who signs it. It is NOT the "Billed To" / "Invoice To" / "To,"
   party, which is the buyer. Do not confuse them. The same applies to GSTIN:
   vendor_gstin is the supplier's.
2a. Separately, return the BUYER exactly as printed in bill_to_name and
   bill_to_gstin - whoever the document is addressed to, even when that is a
   private individual rather than a company. Do not substitute a name you
   expect to see, and do not copy the supplier's details into these fields.
   Return null if the document names no buyer at all.
3. Return the invoice number exactly as printed, keeping punctuation - do not
   tidy "INV/2026/42" into "INV-2026-0042".
4. Return a PO reference only if one is actually printed. If the document does
   not mention a purchase order, return null. Never infer one from context.
5. Normalise dates to YYYY-MM-DD. Return amounts as plain numbers with no
   currency symbols and no thousands separators.
6. If tax is split into CGST and SGST, add them together for tax_total.
7. Set tax_inclusive to true ONLY if the document says the rates already
   include GST.
8. If the document is not a payable invoice - a quotation, purchase order,
   receipt, delivery note, credit note or statement - set is_invoice to false
   and say what it is in document_type.
9. Give an honest confidence per field. If the scan was blurred or a figure was
   ambiguous, say so with a low number. An honest low score is far more useful
   to us than false certainty.

Do not decide whether this invoice should be paid, and do not comment on
whether anything looks wrong. Another system makes that decision. Your only
job is to read the document accurately.
"""
