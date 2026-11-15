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
| 13 | **Superseded in Phase 6 — see #55.** Originally: a vendor not found on the master is `REJECTED`, not held. | The reasoning (paying an unknown vendor is the expensive mistake) was right; the conclusion was too strong. Holding the invoice achieves the same protection without refusing a legitimate new supplier. |

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
| 21 | Uploaded file bytes — PDFs and images alike — are stored **in the database**, not on disk. | Free hosting tiers have ephemeral filesystems; run history and previews must survive a redeploy. |
| 22 | SQLite locally, hosted Postgres (Neon free tier) in production, selected by one `DATABASE_URL`. | Neon's free tier does not expire, unlike Render's 30-day free Postgres, which could lapse before the interview. |
| 23 | No authentication, no multi-tenancy, no email ingestion, no ERP integration. | Explicitly out of scope; listed as next steps instead. |
| 24 | Sample invoices are **generated by a script** (ReportLab), not hand-made, and the scanned sample is verified in code to have no text layer. | Reproducible, and proves the scanned path is genuinely image-only. |
| 25 | No logic anywhere keys off a filename or a sample id; samples are ordinary uploads. | The process must work on any invoice PDF, which is the entire point. |

## Reference data & samples (Phase 2)

| # | Assumption | Rationale |
|---|---|---|
| 26 | A PO's `total_amount` is the committed value **inclusive of GST**, while `po_lines.unit_price` is **pre-tax**. | The total is then directly comparable with an invoice grand total, which is what the balance and over-billing rules work on; line-level price variance is naturally a pre-tax comparison. |
| 27 | GSTINs in the seed data carry **genuine, computed mod-36 check digits**, not plausible-looking strings. | The pipeline validates the checksum, so the reference data has to survive its own validator. |
| 28 | The split-PO scenario uses ₹5,01,500 / ₹2,00,600 / ₹1,80,540 / ₹1,50,450 rather than the round ₹5,00,000 / ₹2,00,000 figures in the brief. | Round grand totals are impossible once 18% GST is applied to a realistic rate × quantity. The structure (two part-bills, then a third that over-bills, now by ₹30,090) is unchanged. The two part-bills carry **different quantities** on purpose — see #45. |
| 29 | Scenario 1b (ambiguous PO inference) is a **text** PDF, not a scanned one. | Scenario 1 already proves the vision path. Keeping 1b text isolates the variable being demonstrated — ambiguity in inference — and keeps the demo quick. |
| 30 | One sample (`03b`, Suryodaya) quotes rates **inclusive of GST**. | The brief calls out "tax may be embedded or separated". The extractor must flag it and validation must back-compute the taxable value before any PO comparison. |
| 31 | `LINE_NOT_ON_PO` is only escalated when the unmatched line exceeds the same ±2%/±₹500 band used for totals. | Otherwise the ₹180 freight line in scenario 4a would force a review and defeat the point of that scenario. Incidental charges below the materiality band are noted, not escalated. |
| 32 | Sample invoices deliberately use **short-form vendor names** ("Bharat Steel Ind.", "Suryodaya Engg Works") that differ from the legal names on the vendor master. | Forces the fuzzy matcher to do real work; exact string equality would pass every sample and prove nothing. |
| 33 | The scanned sample is degraded with grey paper, ±1.4° skew, blur, edge shadows, dust speckle and quality-58 JPEG. | A barely-degraded scan is not evidence that the vision path works. The result is verified in code to have **zero** extractable characters. |
| 34 | `sample_invoices/manifest.json` carries each sample's expected outcome and reason codes. | Drives the UI gallery labels and the end-to-end test assertions from one place — but is never read by the pipeline, which must not know what answer is expected. |
| 35 | Four extra samples beyond the five scenarios (blocked vendor, quotation, missing invoice number, bank mismatch) are generated. | These are the "must not crash" cases; having them as real files means they can be tested, not just claimed. |

## Extraction (Phase 4)

| # | Assumption | Rationale |
|---|---|---|
| 36 | Model is **`gemini-3.5-flash`**, fallback **`gemini-3.5-flash-lite`**, both from env. | Verified against the live model list. Newer previews (3.6–3.8) exist but a demo wants stability over novelty; changing model is a one-line env change. |
| 37 | Every numeric field is requested as a **string** and coerced in our own code. | Models are inconsistent about emitting `107026`, `"107026.00"` or `"₹1,07,026.00"`, sometimes within one response. Coercing centrally means one well-tested path instead of scattered surprises. |
| 38 | A value that cannot be parsed as a number becomes **`None`, never `0`**. | A silent zero in an invoice total is a payment bug; a null is a flagged, visible gap. |
| 39 | The prompt explicitly forbids inferring a PO, guessing absent fields, and forming any view on whether the invoice should be paid. | PO inference is the engine's job and must be scored and shown; a model that volunteers an opinion on payment invites someone to trust it. |
| 40 | The prompt explicitly distinguishes **supplier from buyer**, naming Acme as the buyer. | Reading the "Billed To" party as the vendor is the easiest and costliest extraction error on an invoice. Asserted for all 14 samples. |
| 41 | Field confidence is requested from the model but **treated as a ceiling, not a verdict** — validation-derived checks can only lower it. | Confirmed empirically: on the scanned sample the model returned a flat 0.98 for every field *including a `po_reference` it correctly returned as null*. Self-reported confidence alone is close to useless. |
| 42 | Page images are rendered at **160 dpi, and only for scanned documents**. | Rendering every page of every text PDF would consume most of the run's time budget for nothing. |
| 43 | On HTTP 429 the client switches to the fallback model immediately rather than exhausting retries on the primary. | Per-model quotas are separate; burning the backoff budget on an exhausted model wastes the demo's time. |
| 44 | Fixtures are **real recorded Gemini responses**, and the test suite runs offline against them, with `pytest -m live` as the opt-in real-API run. | Keeps CI deterministic and preserves free-tier quota for demos, while still proving the rules work on genuine model output rather than hand-written convenience data. |

## Pipeline orchestration (Phase 5)

| # | Assumption | Rationale |
|---|---|---|
| 45 | The two split-PO part-bills bill **different quantities** (2000 kg and 1800 kg), not the same amount twice. | Found by running the sequence live: two invoices of the *same* amount from one vendor six days apart correctly trip `DUPLICATE_SUSPECTED`, which masked the over-billing the scenario exists to show. The rule is right; the sample data was wrong. Both behaviours now have regression tests. |
| 46 | Every processed invoice is written to the ledger — approved, held **and** rejected — but only an approval moves the PO balance. | Duplicate detection must be able to see held invoices, otherwise a re-send of something already under review would sail through. |
| 47 | A PO whose invoiced-to-date reaches its total is automatically marked `closed`. | Matches how an AP team works, and means a later invoice against it is rejected rather than silently over-billing. |
| 48 | The pipeline runs in a worker thread; stage events reach the browser over SSE via a thread-safe bus. | PDF parsing and the Gemini call are blocking work. Running them on the event loop would freeze every other request, including the SSE stream itself. |
| 49 | An SSE client receives a **snapshot of stored steps first**, then the live tail. | Removes the race between starting a run and subscribing to it, and means the dashboard replays a past run through exactly the same code path as a live one. |
| 50 | A failure to publish a live event never fails the run. | Progress updates are cosmetic; the decision and its audit trail are persisted regardless. |
| 51 | **Superseded in Phase 7 — see #71.** Originally: uploads are capped at 15 MB at the API. | Comfortably above any real invoice, low enough that a bad upload cannot exhaust the free tier's memory. |
| 52 | Nothing is written to a JSON column without passing through `json_safe()`, which turns Decimals into **strings** (not floats) and dates into ISO text. | A Decimal reaching a JSON column only fails at flush time — i.e. *after* the decision is made, which is the worst possible moment. Found live on scenario 4a. Decimals become strings because this is an audit trail and a float's rounding has no business in one. |
| 53 | The run-failure handler rolls the session back before recording the failure. | A failed flush poisons the session, so the handler's own commit raised `PendingRollbackError` and the run was stranded at `running` forever. Failures must always be *visible*, which means the failure path has to survive the failure. |
| 54 | Re-running an identical sample PDF without resetting is rejected as `DUPLICATE_CONFIRMED_HASH`. | Correct and deliberate: a byte-identical resubmission *is* a duplicate, and the hash check is the most certain signal we have. Found while driving the deployed UI. The operational consequence — reset before a demo — is documented in the README and the demo script rather than papered over by weakening the rule. |

## Vendor handling (Phase 6)

Prompted by a real invoice: a Blinkit consumer receipt was uploaded and came
back `REJECTED` with `VENDOR_NOT_ON_MASTER`. Holding it was correct — the
seller was not on the master, there was no PO, and it was billed to an
individual — but `REJECTED` was the wrong hold, and the single reason code
said only the least interesting of those three things.

| # | Assumption | Rationale |
|---|---|---|
| 55 | **Unknown vendors are routed to review, not rejected, because in real AP they may be legitimate new suppliers awaiting onboarding.** `VENDOR_UNKNOWN` is `NEEDS_REVIEW`; only `VENDOR_BLOCKED` (someone has already decided not to pay this company) and a confirmed duplicate are `REJECTED`. | A rejection says "this is not payable". An unknown supplier is not that — it is an unanswered question, and the answer is an onboarding process, not a refusal. Nothing is paid either way, so the protection is identical; the difference is whether the invoice has somewhere to go next. Supersedes #13. |
| 56 | Vendor verification produces three distinct conclusions rather than one: `VENDOR_UNKNOWN` (not on the master at all → review), `VENDOR_BLOCKED` (on the master, blocked → reject), `VENDOR_PENDING` (on the master, onboarding incomplete → review). | They call for three different human actions. Collapsing them into "vendor problem" pushes the diagnosis back onto the person reading the screen, which is the work the system exists to do. |
| 57 | The `VENDOR_UNKNOWN` finding carries the extracted vendor name, GSTIN, invoice number, date and amount as evidence, and the draft email is written from them. | Procurement should be able to open an onboarding case from the email alone, without opening the PDF. Evidence that is only on screen is evidence that gets retyped, and retyped data is wrong data. |
| 58 | An invoice whose "Bill To" party is neither our GSTIN nor our name above 0.70 raises `BILLED_TO_OTHER_ENTITY` (review). Either identifier matching is enough; a document naming no buyer raises nothing. | A consumer receipt made out to an individual is a perfectly valid document that is not ours to pay — it belongs in expense claims. Absence of a buyer is not evidence of a *different* buyer, so silence must not be read as a mismatch: every fixture recorded before this check existed has no bill-to field, and none of them should start being held because of it. |
| 59 | When the vendor is unknown, stages 6–8 still run and still report. `NO_PO_FOUND` is recorded as a finding, but `VENDOR_UNKNOWN` remains the primary reason shown. | Skipping the later stages would leave a reviewer unable to tell whether a PO was ever looked for. The counterparty codes are declared first inside the `REVIEW` block, which is what makes the identity question the headline without suppressing anything underneath it. |
| 60 | "No PO quoted at all" and "the quoted PO does not exist" are separate codes (`NO_PO_FOUND`, `PO_NOT_FOUND`). | They are different problems with different fixes — one needs procurement approval, the other is a data error — and the single old template read badly for both ("quotes '(none quoted)' but no such PO exists"). |
| 61 | A PO may be raised with no line detail, and reconciliation then checks the total only, saying so via `PO_HAS_NO_LINE_DETAIL`. | Blanket and service POs genuinely carry a committed value and no line items. Comparing invoice lines against a PO that has none would mark every line "not on the PO" and hold the invoice over a gap in *our* reference data rather than anything wrong with the invoice. |
| 62 | Unknown line similarity is not scored as zero similarity. Where neither side carries line descriptions, PO inference redistributes that weight across amount and recency instead. | Found while building the onboarding flow: scoring absent evidence as 0.0 capped any value-only PO at 0.65, below the 0.75 inference floor, so a PO whose amount matched to the paisa could never be matched to the invoice it was raised for. Absent evidence and contrary evidence are not the same thing. |
| 63 | Vendors and POs can be created from the Reference Data page, with the same GSTIN format and checksum validation the pipeline applies to invoices. | Onboarding is the recommended next action for the most interesting hold in the system; being unable to carry it out makes that recommendation theatre. Validating the GSTIN where the typo is made beats surfacing it later as a `GSTIN_MISMATCH` against an invoice that was perfectly correct. |
| 64 | A vendor added on screen is stored in the same table, in the same shape, as a seeded one — including aliases — and "Reset demo data" removes it. | If an onboarded vendor behaved differently from a seeded one, the demo would be a special case rather than a demonstration. |
| 65 | Two vendor rows may not share a GSTIN. | GSTIN is the identifier vendor matching tries first and treats as decisive. Two rows holding one GSTIN would make the match depend on row order, which is the one thing a decision engine must never do. |
| 66 | **Re-run** replays the stored model response rather than re-reading the document, and withdraws the superseded run's ledger entry. | Two reasons. It isolates the variable: if the outcome changes, it changed because the reference data changed, not because the model read the page differently the second time. And it keeps a re-run free and instant, which matters on a free tier. Withdrawing the ledger entry is not cosmetic — without it the document matches itself by file hash and comes back `DUPLICATE_CONFIRMED_HASH`, the same trap documented in #54. |
| 67 | A replayed extraction is not counted in the AI quota meter, and the extraction stage says out loud that it was replayed. | A call that never left the building must not appear in the counter checked before a demo, and a stage summary that claimed to have read the document would be a lie by omission. |
| 68 | **Resolved in Phase 7.** The buyer GSTIN in `config/rules.yaml` (`27AAACA1111A1ZS`) is checksum-valid, and the sample generator was corrected to match. The samples generated before that change still printed the earlier, invalid check digit; adding the photo sample regenerated the whole gallery, so every sample now prints the correct one. | Regenerating all of them to fix a digit that nothing validates would have rewritten every committed sample PDF for no behavioural gain. The bill-to check accepts *either* our GSTIN or our name, and those samples all print our name correctly, so none of them is affected. A future full regeneration makes it consistent. |


## Image invoices (Phase 7)

Prompted by the obvious gap: real invoices arrive as phone photos and
screenshots at least as often as they arrive as tidy PDFs, and the system
accepted only PDFs. Images now run the same ten stages and produce the same
kind of decision.

| # | Assumption | Rationale |
|---|---|---|
| 69 | Accepted formats are **PDF, PNG, JPG/JPEG and WEBP**, declared in `config/rules.yaml` (`ingest.accepted_formats`) rather than in code. | It is a policy about what the AP inbox will take, not an implementation detail, so it belongs in the file the Reference Data page displays. |
| 70 | The file's type is detected from its **magic bytes**, never from its extension. A `.txt` renamed `.png` fails as the text file it is; a PDF renamed `.png` is processed as the PDF it is. | Extensions are a naming convention, not a fact about the content. Trusting one means handing arbitrary bytes to an image decoder because someone renamed a file. |
| 71 | The file-size limit is **10 MB** (`ingest.max_file_mb`), enforced inside the pipeline so an oversized upload becomes a **`FAILED` run with `FILE_TOO_LARGE`** rather than a dismissible error toast. The API drops anything over twice that at the door. **Supersedes #51** (15 MB, API-only). | A refusal is a decision about a document someone sent us, and decisions belong on the dashboard with the rest of the audit trail. The outer ceiling exists only so a pathological upload is not read into memory and stored first. |
| 72 | An image **skips the text-layer check entirely** and goes straight to the vision path; `IMAGE_UPLOAD` records that, rather than `SCANNED_DOCUMENT`. | An image has no text layer to measure, so reporting "no text layer found (0 chars/page)" about one would imply a test that was never run. The two codes describe genuinely different situations. |
| 73 | Before an image is sent to the model, **EXIF orientation is applied** and the long edge is capped at **2000 px**. Both the original and processed dimensions are recorded on the stage. | A phone stores the sensor's view plus a tag saying which way is up; without honouring it the model reads a sideways invoice, which is the most common reason a photographed invoice extracts badly. The cap is about cost: beyond ~2000 px nothing more becomes legible, and every extra pixel is free-tier tokens. Recording both sizes means the screen shows what was actually sent, not what was uploaded. |
| 74 | The upright, downscaled image is re-encoded as **JPEG** before sending, whatever it arrived as, while the **original bytes are stored unchanged**. | The processed copy exists only to be read by the model; the stored original is what the preview panel shows and what a re-run re-reads, so it must not be degraded. The file hash used for duplicate detection is of the original bytes, which makes duplicate detection format-agnostic. |
| 75 | An image whose **shorter side is under 600 px** raises `LOW_IMAGE_QUALITY` at `REVIEW` severity, so it cannot auto-approve. | At that size invoice text is at the edge of readable, and an extraction nobody can verify is not a basis for releasing payment. It is a hold, not a refusal: a human can read the image and confirm. |
| 76 | **Single-page images only.** One image is one invoice; a multi-page invoice must arrive as a PDF, and submitting several images as one invoice is out of scope. | Multi-image submission needs a grouping decision (which photos belong to which invoice, in what order) that is a different problem from reading one. A multi-page PDF is already handled page by page. |
| 77 | The sample gallery includes a **generated phone photo** (`10_phone_photo_vega.jpg`) — the page on a desk, at an angle, lit unevenly — of an invoice that matches an open PO exactly, so it must come back `APPROVED`. | The vision path should be demonstrable on something that looks like what an AP clerk is really sent, and the sample is uninteresting apart from its format: if it is held, the image path is at fault and nothing else. |
| 78 | The recorded fixtures were **not** re-captured when the gallery was regenerated; only the new photo sample cost a call. | The regeneration changed one character on each sample (the buyer GSTIN check digit, #68) and the PDF creation timestamp. Nothing the extraction reads changed, the fixtures still validate against the schema and every scenario still reaches its expected outcome, so spending fifteen free-tier calls to re-record identical answers would have bought nothing. |
| 79 | A new column on an existing table is added by an **additive check at boot** (`_add_missing_columns`), not by dropping and reseeding. | `create_all()` creates missing tables but never alters existing ones, so `runs.file_kind` would have been missing on the deployed Postgres. Reseeding would have taken the run history with it. Anything beyond adding a defaulted column would want a real migration tool. |
