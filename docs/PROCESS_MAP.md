# Process Map — AI Invoice Processing Pipeline

**Company:** Acme Manufacturing Pvt Ltd (fictional, India) · **Currency:** INR · **Tax:** GST
**Matching model:** 2-way match (Invoice ↔ Purchase Order)

---

## 1. Design principle

> **The LLM reads. Deterministic code decides.**

| Concern | Owner | Why |
|---|---|---|
| Reading a messy PDF into structured fields | Gemini (vision/text), schema-constrained, `temperature=0` | Layout variation and scans are a perception problem; no rule can parse every vendor template. |
| Deciding APPROVE / REVIEW / REJECT | Pure-Python rules engine, no LLM calls | Auditability, reproducibility, and control of financial risk. Same input + same reference data ⇒ same decision, every time. |
| Phrasing the decision in plain English | Deterministic templates filled with reason codes + evidence | No second LLM call (free-tier budget), and the words can never drift from the decision. |

Exactly **one LLM call per run** (extraction). Everything downstream is deterministic and unit-testable without network access.

---

## 2. End-to-end flow

```mermaid
flowchart TD
    A[Invoice PDF uploaded<br/>or sample selected] --> S1

    S1[1 · INGEST<br/>SHA-256 hash, size, page count,<br/>store bytes in DB]
    S1 -->|not a PDF / empty / corrupt| F[FAILED<br/>clear message + Retry]
    S1 --> S2

    S2{2 · DOCUMENT ANALYSIS<br/>chars of text per page}
    S2 -->|at least 120 chars/page| T[Text path:<br/>send extracted text]
    S2 -->|below 120 chars/page| V[Scanned path:<br/>render pages to images]

    T --> S3
    V --> S3
    S3[3 · EXTRACTION · LLM<br/>strict JSON schema,<br/>per-field confidence,<br/>is_invoice flag]
    S3 -->|retries exhausted / HTTP 429| F
    S3 -->|document is not an invoice| R0[REJECTED<br/>NOT_AN_INVOICE]
    S3 --> S4

    S4[4 · VALIDATION and NORMALISATION<br/>dates, amounts, invoice no., PO ref<br/>line total = qty x price<br/>sum of lines = subtotal<br/>subtotal + tax = total<br/>tax-inclusive detection] --> S5

    S5[5 · VENDOR VERIFICATION<br/>GSTIN exact match, else fuzzy<br/>name/alias score<br/>status + bank account check]
    S5 -->|blocked / not on master| R1[REJECTED]
    S5 --> S6

    S6[6 · DUPLICATE DETECTION<br/>a. same file hash<br/>b. same vendor + normalised inv. no.<br/>c. same vendor + amount, within 7 days]
    S6 -->|a or b| R2[REJECTED<br/>DUPLICATE_CONFIRMED]
    S6 --> S7

    S7[7 · PO MATCHING<br/>explicit ref: normalise and look up<br/>missing/unknown: infer from vendor +<br/>open POs + amount + line similarity,<br/>ranked and scored]
    S7 -->|no candidate| N1[NEEDS_REVIEW<br/>PO_NOT_FOUND]
    S7 -->|top two scores close| N2[NEEDS_REVIEW<br/>PO_INFERRED_AMBIGUOUS<br/>candidates listed]
    S7 --> S8

    S8[8 · RECONCILIATION<br/>line level: qty, unit price<br/>total level vs PO REMAINING balance<br/>tolerances from rules.yaml] --> S9

    S9{9 · DECISION ENGINE<br/>aggregate all findings<br/>severity precedence}
    S9 -->|any REJECT finding| D1[REJECTED]
    S9 -->|any REVIEW finding| D2[NEEDS_REVIEW]
    S9 -->|all checks pass| D3[APPROVED]

    D3 --> U[Update ledger and<br/>PO amount_invoiced_to_date]
    D1 --> S10
    D2 --> S10
    U --> S10
    R0 --> S10
    R1 --> S10
    R2 --> S10
    N1 --> S10
    N2 --> S10

    S10[10 · OUTPUT<br/>decision card, reason codes + evidence,<br/>recommended next action,<br/>draft email, audit JSON] --> END[Run persisted ·<br/>visible on dashboard]
    F --> END
```

Every stage streams `pending → running → passed | warning | failed | skipped` over **Server-Sent Events**, and is persisted as a `RunStep` row with start/end time, duration, a one-line human summary and a structured detail payload.

---

## 3. Decision outcomes

| Outcome | Meaning | Side effects |
|---|---|---|
| **APPROVED** | All checks passed, or only within-tolerance variances. Ready for payment. | Ledger entry written; PO `amount_invoiced_to_date` incremented. |
| **NEEDS_REVIEW** | A human must look. Output states *exactly what* and *why*, with evidence numbers and a suggested next action. | Ledger entry written as `held`; PO balance **not** moved. |
| **REJECTED** | Clear policy violation. | Ledger entry written as `rejected`; PO balance not moved. |
| **FAILED** | The system could not process the input (corrupt file, provider down after retries). Shown honestly, with a Retry button. | Nothing written to ledger. |

### Severity precedence

`REJECT > REVIEW > APPROVE`

The engine collects **all** findings from every stage — it never short-circuits, because a reviewer should see everything that was wrong, not just the first thing. The outcome is the highest severity present. Reason codes are then sorted by severity and by their declaration order in the table below, so the "top reason" shown on the dashboard is always the most consequential one.

---

## 4. Tolerance policy (`config/rules.yaml`)

| Rule | Threshold | Rationale |
|---|---|---|
| Invoice total vs expected amount | within **±2% or ±₹500, whichever is smaller** | Absorbs rounding and small freight differences without absorbing real price creep. The "whichever is smaller" guard stops the percentage band from becoming large on big invoices. |
| Line-item unit price variance | **> 5%** above PO price → hold | A per-line check catches a single inflated SKU that a total-level check would hide. |
| Line-item quantity variance | **> 2%** above PO qty → hold | Over-delivery needs procurement sign-off. |
| Cumulative billing against a PO | must not exceed `total_amount` + tolerance | Supports legitimate split invoicing while blocking over-billing. |
| Suspected-duplicate window | same vendor + same total within **7 days** | Catches re-sends issued under a fresh invoice number. |
| Minimum field confidence | **0.70** on critical fields | Below this, route to a human rather than guess. |
| Vendor fuzzy-name match | **≥ 0.88** auto-accept · **0.70–0.88** review · **< 0.70** no match | Name variants ("Bharat Steel Ind.") must match; unrelated vendors must not. |
| PO inference | top score **≥ 0.75** and ahead of the runner-up by **≥ 0.15** | A confident single candidate proceeds and says it was inferred; anything closer is ambiguous → review. |

All thresholds are read from one YAML file at runtime and displayed on the Reference Data page, so they are visibly policy, not code.

---

## 5. Reason code reference

Every code carries a `severity`, a human-readable message template, and the **evidence** — the actual numbers that were compared.

### REJECT

| Code | Fires when | Evidence shown |
|---|---|---|
| `NOT_AN_INVOICE` | Model flags the document as a quote / receipt / credit note / other | Detected document type |
| `DUPLICATE_CONFIRMED_HASH` | Byte-identical file already processed | Original run id, date, outcome |
| `DUPLICATE_CONFIRMED_NUMBER` | Same vendor + same normalised invoice number in ledger | Both raw numbers, the normalised form, original date and amount |
| `VENDOR_BLOCKED` | Matched vendor has status `blocked` | Vendor id, status |
| `VENDOR_NOT_ON_MASTER` | No vendor match scores ≥ 0.70 | Invoice vendor name, best candidate and score |
| `PO_CLOSED` | Referenced PO has status `closed` | PO number, status, amount already invoiced |

### NEEDS_REVIEW

| Code | Fires when | Evidence shown |
|---|---|---|
| `PO_OVERBILLED` | Invoice total exceeds PO remaining balance + tolerance | PO total, invoiced to date, remaining, invoice total, overage |
| `PRICE_VARIANCE_EXCEEDS_TOLERANCE` | A line's unit price exceeds the PO price by more than 5% | Per line: description, PO price, invoice price, % variance |
| `QTY_VARIANCE_EXCEEDS_TOLERANCE` | A line's quantity exceeds PO quantity by more than 2% | Per line: PO qty, invoice qty, difference |
| `TOTAL_VARIANCE_EXCEEDS_TOLERANCE` | Total falls outside the ±2% / ±₹500 band | Expected, actual, difference, band applied |
| `VENDOR_BANK_MISMATCH` | Bank account on the invoice differs from vendor master | Last 4 on invoice vs last 4 on master |
| `VENDOR_PENDING` | Vendor status is `pending` | Vendor id, status |
| `VENDOR_FUZZY_MATCH_LOW` | Name match score between 0.70 and 0.88 | Invoice name, matched name, score |
| `GSTIN_MISMATCH` | GSTIN on invoice differs from master GSTIN for the matched vendor | Both GSTINs |
| `GSTIN_INVALID_FORMAT` | GSTIN fails the 15-character format/checksum test | The GSTIN as read |
| `DUPLICATE_SUSPECTED` | Same vendor + same total within 7 days, different number | Both invoice numbers, dates, amount |
| `PO_NOT_FOUND` | A reference was present but matched no PO, and inference found nothing | PO ref as written and normalised |
| `PO_INFERRED_AMBIGUOUS` | Two or more inference candidates score close together | Each candidate with its score and score components |
| `PO_INFERRED_LOW_CONFIDENCE` | Best inference candidate scores below 0.75 | Candidate, score, components |
| `PO_VENDOR_MISMATCH` | The PO belongs to a different vendor than the invoice | PO vendor, invoice vendor |
| `LINE_NOT_ON_PO` | An invoiced line has no counterpart on the PO | Line description, amount |
| `MISSING_INVOICE_NUMBER` | Critical field absent from the document | — |
| `MISSING_INVOICE_DATE` | Critical field absent from the document | — |
| `MISSING_TOTAL` | Critical field absent from the document | — |
| `LOW_CONFIDENCE_CRITICAL_FIELD` | Model confidence below 0.70 on a critical field | Field name, confidence |
| `ARITHMETIC_LINES_VS_SUBTOTAL` | Sum of line amounts ≠ stated subtotal (beyond ₹1 rounding) | Sum of lines, stated subtotal, difference |
| `ARITHMETIC_SUBTOTAL_TAX_TOTAL` | subtotal + tax ≠ total (beyond ₹1 rounding) | Each component and the gap |
| `CURRENCY_MISMATCH` | Invoice currency differs from PO currency | Both currencies |

### INFO — recorded and shown, never blocks

| Code | Fires when |
|---|---|
| `TOTAL_VARIANCE_WITHIN_TOLERANCE` | Total differs from expected but sits inside the band |
| `PO_INFERRED_HIGH_CONFIDENCE` | The PO was inferred rather than quoted — stated explicitly on the decision card |
| `PARTIAL_INVOICE_AGAINST_PO` | The invoice bills part of an open PO; remaining balance is shown |
| `TAX_INCLUSIVE_PRESENTATION` | Prices are presented inclusive of GST; normalised before comparison |
| `SCANNED_DOCUMENT` | No text layer was found; the vision extraction path was used |

### FAILED

| Code | Fires when |
|---|---|
| `FILE_NOT_PDF` / `FILE_EMPTY` / `FILE_CORRUPT` / `FILE_ENCRYPTED` | Ingest cannot open the input |
| `LLM_RATE_LIMITED` | HTTP 429 persists after retries and the fallback model |
| `LLM_UNAVAILABLE` | Provider error or timeout after retries |
| `EXTRACTION_SCHEMA_INVALID` | Model output fails schema validation after retries |

---

## 6. Scenario → expected outcome

| # | Scenario | Expected outcome | Key reason codes |
|---|---|---|---|
| 0 | Happy path — clean text PDF, approved vendor, explicit PO, amounts match | `APPROVED` | — (all checks pass) |
| 1 | Scanned image-only PDF, no PO reference | `APPROVED` | `SCANNED_DOCUMENT`, `PO_INFERRED_HIGH_CONFIDENCE` |
| 1b | Same, but two similar open POs exist | `NEEDS_REVIEW` | `PO_INFERRED_AMBIGUOUS` |
| 2 | Split PO — third invoice exceeds the remaining balance | #1, #2 `APPROVED`; #3 `NEEDS_REVIEW` | `PARTIAL_INVOICE_AGAINST_PO`, then `PO_OVERBILLED` |
| 3 | Disguised duplicate — `INV/2026/42` vs `INV-2026-0042` | `REJECTED` | `DUPLICATE_CONFIRMED_NUMBER` |
| 3b | Different number, same amount, within 7 days | `NEEDS_REVIEW` | `DUPLICATE_SUSPECTED` |
| 4a | Total off by ₹180 from freight/rounding | `APPROVED` | `TOTAL_VARIANCE_WITHIN_TOLERANCE` |
| 4b | One line's unit price raised 8% against the PO | `NEEDS_REVIEW` | `PRICE_VARIANCE_EXCEEDS_TOLERANCE` |

Additional cases that must be handled without crashing (not featured in the video): non-PDF upload, encrypted or corrupt PDF, a quotation instead of an invoice, a blocked vendor, a bank-detail mismatch, and an LLM timeout.
