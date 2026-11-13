# Assumptions

Every assumption made where the problem statement was silent, with the reasoning behind it.
Decided in Phase 1 unless noted; later phases append rather than rewrite.

## Domain & setting

| # | Assumption | Rationale |
|---|---|---|
| 1 | The buyer is **Acme Manufacturing Pvt Ltd**, a fictional Indian mid-size manufacturer. | One coherent setting makes the data, tax treatment and currency consistent everywhere. |
| 2 | Currency is **INR** throughout; a non-INR invoice raises `CURRENCY_MISMATCH` rather than converting. | FX conversion is a separate policy decision an AP clerk should not automate silently. |
| 3 | Tax is **GST at 18% standard**, with 12% and 5% appearing on some vendors. Vendors are identified by **GSTIN** (15 characters, checkable format). | GSTIN gives a strong, machine-checkable identity key, so vendor matching is not purely name-based. |
| 4 | Matching is **2-way** (invoice ↔ PO). Goods-receipt (3-way) matching is listed as future work. | A goods-receipt dataset would double the reference data without adding a new *kind* of judgement to demonstrate. |
| 5 | An invoice belongs to exactly **one PO**. Multi-PO invoices are out of scope and would route to review via `LINE_NOT_ON_PO`. | Common in mid-size AP; multi-PO allocation is a materially larger problem. |
| 6 | Payment terms come from the vendor master, not the invoice. The invoice's own due date is extracted and shown, but the recommended payment date is computed from master terms. | Vendors sometimes state terms more favourable to themselves than the agreed ones. |

## Decision policy

| # | Assumption | Rationale |
|---|---|---|
| 7 | Exactly three outcomes — `APPROVED`, `NEEDS_REVIEW`, `REJECTED` — plus `FAILED` for processing errors. | Matches how an AP queue actually works: pay it, look at it, or refuse it. |
| 8 | Severity precedence is `REJECT > REVIEW > APPROVE`, and the engine evaluates **all** rules rather than short-circuiting on the first failure. | A reviewer needs the full picture; stopping early would hide a second, worse problem. |
| 9 | Tolerance is **±2% or ±₹500, whichever is smaller**. | The percentage band alone becomes too generous on large invoices; the rupee cap bounds absolute exposure. |
| 10 | Only **over**-billing breaches tolerance. An invoice *below* the PO amount is treated as partial billing, not an error. | Under-billing carries no financial risk to the buyer. |
| 11 | `APPROVED` means "approved for payment posting" — the system posts nothing to an ERP and moves no money. | Explicitly out of scope; the output is a decision plus a recommended action. |
| 12 | A confirmed duplicate is `REJECTED`, not held. A *suspected* duplicate is held for review. | Byte-identical or same-normalised-number resubmissions are unambiguous; coincidental same-amount invoices are not. |
| 13 | A vendor not found on the master is `REJECTED` (`VENDOR_NOT_ON_MASTER`), not held. | Paying an unknown vendor is the single most expensive AP mistake; onboarding is a separate process (PS-2). |

## Extraction & AI

| # | Assumption | Rationale |
|---|---|---|
| 14 | **One** LLM call per run, for extraction only. Explanations and draft emails are deterministic templates filled from reason codes and evidence. | Keeps the demo inside the Gemini free tier, and guarantees the words on screen can never contradict the decision. |
| 15 | The LLM never decides, never infers a PO, and never fills in a field that is absent from the document — missing fields come back `null` with a flag. | Auditability and reproducibility; invented data is worse than missing data in finance. |
| 16 | Extraction runs at `temperature=0` with a strict response schema, and is validated with Pydantic before anything downstream sees it. | Determinism as far as the model allows, and a hard boundary against malformed output. |
| 17 | A page yielding fewer than **120 extractable characters** is treated as scanned, and all pages are sent as images. | Chosen empirically against the generated samples; a header-only text layer on a scan would otherwise pass as a text PDF. |
| 18 | Per-field confidence is the **lower** of the model's self-reported confidence and a validation-derived score (arithmetic consistency, format checks). | Self-reported confidence alone is unreliable; cross-checking against arithmetic gives an independent signal. |
| 19 | Tests run against **recorded fixtures**, with a separate opt-in `--live` run against the real API. | Free-tier quota must be reserved for demos, and CI must be deterministic. |

## Data & infrastructure

| # | Assumption | Rationale |
|---|---|---|
| 20 | Reference data (vendors, POs, PO lines, historical ledger) is seeded from CSVs in `data/`, idempotently on boot, with a **Reset demo data** endpoint. | Demos must start from a known state, repeatably. |
| 21 | Uploaded PDF bytes are stored **in the database**, not on disk. | Free hosting tiers have ephemeral filesystems; run history and previews must survive a redeploy. |
| 22 | SQLite locally, hosted Postgres (Neon free tier) in production, selected by one `DATABASE_URL`. | Neon's free tier does not expire, unlike Render's 30-day free Postgres, which could lapse before the interview. |
| 23 | No authentication, no multi-tenancy, no email ingestion, no ERP integration. | Explicitly out of scope; listed as next steps instead. |
| 24 | Sample invoices are **generated by a script** (ReportLab), not hand-made, and the scanned sample is verified in code to have no text layer. | Reproducible, and proves the scanned path is genuinely image-only. |
| 25 | No logic anywhere keys off a filename or a sample id; samples are ordinary uploads. | The process must work on any invoice PDF, which is the entire point. |

## Reference data & samples (Phase 2)

| # | Assumption | Rationale |
|---|---|---|
| 26 | A PO's `total_amount` is the committed value **inclusive of GST**, while `po_lines.unit_price` is **pre-tax**. | The total is then directly comparable with an invoice grand total, which is what the balance and over-billing rules work on; line-level price variance is naturally a pre-tax comparison. |
| 27 | GSTINs in the seed data carry **genuine, computed mod-36 check digits**, not plausible-looking strings. | The pipeline validates the checksum, so the reference data has to survive its own validator. |
| 28 | The split-PO scenario uses ₹5,01,500 / ₹2,00,600 × 2 / ₹1,50,450 rather than the round ₹5,00,000 / ₹2,00,000 figures in the brief. | Round grand totals are impossible once 18% GST is applied to a realistic rate × quantity. The structure (two part-bills, then a third that over-bills by ₹50,150) is unchanged. |
| 29 | Scenario 1b (ambiguous PO inference) is a **text** PDF, not a scanned one. | Scenario 1 already proves the vision path. Keeping 1b text isolates the variable being demonstrated — ambiguity in inference — and keeps the demo quick. |
| 30 | One sample (`03b`, Suryodaya) quotes rates **inclusive of GST**. | The brief calls out "tax may be embedded or separated". The extractor must flag it and validation must back-compute the taxable value before any PO comparison. |
| 31 | `LINE_NOT_ON_PO` is only escalated when the unmatched line exceeds the same ±2%/±₹500 band used for totals. | Otherwise the ₹180 freight line in scenario 4a would force a review and defeat the point of that scenario. Incidental charges below the materiality band are noted, not escalated. |
| 32 | Sample invoices deliberately use **short-form vendor names** ("Bharat Steel Ind.", "Suryodaya Engg Works") that differ from the legal names on the vendor master. | Forces the fuzzy matcher to do real work; exact string equality would pass every sample and prove nothing. |
| 33 | The scanned sample is degraded with grey paper, ±1.4° skew, blur, edge shadows, dust speckle and quality-58 JPEG. | A barely-degraded scan is not evidence that the vision path works. The result is verified in code to have **zero** extractable characters. |
| 34 | `sample_invoices/manifest.json` carries each sample's expected outcome and reason codes. | Drives the UI gallery labels and the end-to-end test assertions from one place — but is never read by the pipeline, which must not know what answer is expected. |
| 35 | Four extra samples beyond the five scenarios (blocked vendor, quotation, missing invoice number, bank mismatch) are generated. | These are the "must not crash" cases; having them as real files means they can be tested, not just claimed. |
