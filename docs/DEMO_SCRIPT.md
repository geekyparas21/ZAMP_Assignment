# Demo Script

Two run orders for the same system: a **5-minute video** and a **live
interview** walkthrough. The optional edge cases at the end are what to reach
for when someone asks "what if…", or uploads an invoice of their own.

**Live:** https://invoice-pipeline-837p.onrender.com

---

## Before you start

| Step | Why |
|---|---|
| Open the live URL and wait for the first page to paint. | Free-tier hosting spins down when idle; the first request can take 30–50 seconds. Never let that happen on camera. |
| **Reference → Reset demo data → Yes, reset.** | Re-running a sample that has already been processed is correctly rejected as `DUPLICATE_CONFIRMED_HASH` — the file is byte-identical, so it *is* a duplicate. Reset restores the seeded state. This is the single most common way to ruin a demo. |
| Check **Reference → AI usage, last 24h**. | Each run spends one Gemini call. Confirm there is quota left before recording. |
| Pick a colour theme (top right). | Light reads better on a projector; dark reads better on a screen share. |

---

## 5-minute video

> The thread to hold on to: **the model reads, deterministic code decides.**
> Say it once at the start and let each scenario prove it.

### 0:00 — What this is (30s)

Open on **New run**.

> "An invoice arrives as a PDF. This system reads it, checks it against the
> approved vendor list and the open purchase orders, and gives a decision
> you can explain — approve, hold for review, or reject. The AI does exactly
> one job: it reads the document. Every decision after that is deterministic
> Python, so the same invoice and the same reference data always produce the
> same answer."

### 0:30 — The happy path (60s)

Click sample **00 · Bharat Steel Industries**.

Let the stage timeline run live. Point at it while it does:

> "Ten stages, streaming as they happen. Ingest, then it works out this is a
> text PDF rather than a scan, then one AI call to extract the fields, then
> validation, vendor, duplicates, PO matching, reconciliation, decision."

When it lands on **APPROVED**:

- Point at **Why this decision** — every check that fired, worst first.
- Expand the **Reconciliation** stage: the invoice total against the PO's
  *remaining* balance, line by line.
- Point at **Purchase order updated**: the PO's invoiced-to-date moved.

> "Approved, and it says why: vendor matched by GSTIN, PO quoted on the
> invoice, amounts reconcile to the paisa."

### 1:30 — A scan with no PO number (60s)

Click sample **01 · Deccan Precision Tools**.

> "This one is a photograph of an invoice — no text layer at all — and it
> doesn't quote a PO number."

When it lands on **APPROVED**:

- `SCANNED_DOCUMENT`: no text layer, so the pages went to the vision model.
- `PO_INFERRED_HIGH_CONFIDENCE`: expand it and read the score.

> "It inferred the PO from the vendor's open orders, scored on amount and line
> similarity — and it says out loud that it inferred it rather than quietly
> pretending the vendor quoted it. Below its confidence threshold, or too
> close to a second candidate, it goes to a human instead. Sample 01b is
> exactly that case."

### 2:30 — A duplicate wearing a disguise (45s)

Click sample **03 · Vega Instruments**.

> "Invoice `INV/2026/42`. We already paid `INV-2026-0042` in October. Different
> punctuation, different leading zeros, same invoice."

**REJECTED**, `DUPLICATE_CONFIRMED_NUMBER`. Show the evidence: both raw
numbers and the normalised form they collapse to.

> "Byte-identical files and same-vendor-same-number are certain, so they're
> rejected. Same vendor and same *amount* within a week but a different
> number is only suspicious — that's held for a human, never auto-rejected,
> because a vendor can legitimately deliver the same goods twice in a week."

### 3:15 — Tolerance versus real price creep (60s)

Click **04a · Trident Fasteners** → **APPROVED**,
`TOTAL_VARIANCE_WITHIN_TOLERANCE`, ₹180 over on freight.

Then **04b · Kaveri Logistics** → **NEEDS_REVIEW**,
`PRICE_VARIANCE_EXCEEDS_TOLERANCE`.

> "Same kind of variance, opposite outcomes. ₹180 of freight rounding is
> inside policy — two percent or five hundred rupees, whichever is smaller.
> An eight percent rise in a unit rate against the PO is not, so it's held,
> with the PO rate and the invoiced rate side by side."

Scroll to the **draft email** and copy it.

> "And the output isn't just a verdict — it's the email, written from the
> same evidence, ready to send to the vendor."

### 4:15 — Where the policy lives (30s)

Go to **Reference → Policy & tolerances**.

> "Every threshold the engine applies is in one YAML file, loaded at runtime
> and displayed here. An AP manager can read it and change it without a
> developer — and because the screen reads the same file the engine does,
> what it claims and what it applies cannot drift apart."

Click **Reason codes**.

> "And every conclusion the system can reach is one of these, each with the
> action it implies."

### 4:45 — Close (15s)

Go to **Dashboard**.

> "Every run is persisted with its full audit trail — stage timings, the model
> response, the evidence behind each finding, downloadable as JSON. The AI
> read the documents. The decisions were all code."

---

## Live interview order

Shorter, and built for interruption. Do **00**, then **01**, then ask which
they want: duplicates, tolerances, or the edge cases below. Keep the
**Reference** page one tab away — most questions are answered by showing the
vendor master, a PO's remaining balance, or the policy file.

If they want to upload their own invoice, say yes. Then read the next section
first.

---

## Optional edge case — unknown vendor → onboard → re-run

**Use this when the interviewer uploads their own invoice**, or asks what
happens with a supplier the system has never seen. It is the most convincing
thing in the demo, because it ends with the decision changing for a stated
reason.

### 1. Upload an invoice from a supplier we have never paid

Use sample **09 · Meridian Industrial Supplies**, or anything they hand you.

**NEEDS_REVIEW.** The decision card lists every finding, worst first:

| Finding | What it says |
|---|---|
| `VENDOR_UNKNOWN` — badged **Primary** | No vendor on the master matches by GSTIN, and the closest name scored far below the threshold. |
| `NO_PO_FOUND` | No PO quoted, and none could be inferred — there are no POs to search, because the vendor is unknown. |

> "Held, not rejected — and that distinction is the point. An unknown supplier
> isn't a policy violation, it's an unanswered question. In a real AP team it
> goes to procurement for onboarding. The only things this system *rejects* are
> a vendor someone has already blocked, and a confirmed duplicate."

Scroll to the draft email:

> "And it has already written the onboarding request — vendor name, GSTIN,
> invoice number, date and amount, all taken from the document rather than
> retyped. Procurement can open a case from this email without opening the
> PDF."

Point at `NO_PO_FOUND` sitting underneath:

> "Note it still ran the PO matching and told us what it found. It doesn't
> stop at the first problem — a reviewer gets the whole picture, with the most
> consequential reason at the top."

### 2. Onboard the vendor on screen

**Reference → Vendors → Add a vendor.** Fill in from the decision card:

| Field | Value (for sample 09) |
|---|---|
| Legal name | `Meridian Industrial Supplies Private Limited` |
| GSTIN | `27AAMCM1234N1Z5` |
| Also known as | `Meridian Industrial Supplies` |
| Bank account (last 4) | `6690` |

Before submitting, **change one character of the GSTIN** and try to save:

> "Same format and check-digit test the pipeline applies to invoices — caught
> where the typo is made, rather than surfacing next month as a GSTIN mismatch
> on an invoice that was perfectly correct."

Fix it and save.

### 3. Raise a PO

**Reference → Purchase orders → Add a purchase order.**

| Field | Value |
|---|---|
| Vendor | the one just added |
| Committed value | `87320.00` (match the invoice total) |
| Line detail | leave empty |

> "Line detail is optional, because blanket and service POs genuinely carry a
> committed value and no line items. The decision will say so rather than
> reporting every invoice line as 'not on the PO'."

### 4. Re-run

Back to the run (**Dashboard → Re-run** on the row, or **Re-run** on the run
itself).

**APPROVED.**

> "Same document, same extracted fields — the stored model response is
> replayed, so no new AI call was made and the model can't have read it
> differently the second time. The only thing that changed is the reference
> data. That's what makes this a demonstration rather than a coincidence."

Point at the findings:

- `PO_INFERRED_HIGH_CONFIDENCE` — the new PO was matched on amount.
- `PO_HAS_NO_LINE_DETAIL` — honest about what it could and could not check.
- `ALL_CHECKS_PASSED`.

### 5. Afterwards

**Reference → Reset demo data** before the next run, so the vendor and PO you
added are cleared.

---

## Other questions worth having ready

| "What if…" | Show |
|---|---|
| …the invoice is a consumer receipt, billed to a person? | `BILLED_TO_OTHER_ENTITY` alongside `VENDOR_UNKNOWN`. A grocery bill addressed to an individual is a valid document that isn't ours to pay — it belongs in expense claims. Upload any real shop receipt to show it. |
| …the vendor is blocked? | Sample **05 · Orient Electricals** → `REJECTED`, `VENDOR_BLOCKED`. The contrast with `VENDOR_UNKNOWN` is the whole policy in one pair of runs. |
| …it isn't an invoice at all? | Sample **06** (a quotation) → `REJECTED`, `NOT_AN_INVOICE`. No payment is due on a quote. |
| …the vendor changed their bank details? | Sample **08** → `NEEDS_REVIEW`, `VENDOR_BANK_MISMATCH`, with the "phone a number you already hold" warning. The most common payment-redirection fraud. |
| …someone uploads a PNG, or a corrupt PDF? | It fails honestly — `FILE_NOT_PDF` with a clear message — rather than guessing. |
| …the invoice splits across several deliveries? | Samples **02a → 02b → 02c** in order. The first two part-bills approve and move the PO balance; the third is judged against what is *left* and held as `PO_OVERBILLED`. |
| …the AI misreads a field? | **Reference → Policy**: critical fields below 0.70 confidence are routed to a human. And confidence is a ceiling, not a verdict — validation checks can only lower what the model claims. |
| …how much does a run cost? | One Gemini call per run; the live counter is on the Reference page. A re-run costs nothing. |
