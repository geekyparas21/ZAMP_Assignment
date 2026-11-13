# AI Invoice Processing Pipeline

From invoice PDF to a reasoned, auditable AP decision — with every step visible.

Built for the Zamp AI Solutions Analyst case study (**PS-1 · Finance / AP**).

**Live: https://invoice-pipeline-837p.onrender.com**

---

## What it does

A mid-size manufacturer receives hundreds of vendor invoices a month as PDFs. An AP clerk opens
each one, hunts for the matching purchase order, checks whether the numbers line up, and decides
what to do. This system does that end-to-end, in about ten seconds, and shows its working.

Upload an invoice PDF (clean text or a scanned image) and the pipeline runs ten visible stages:
ingest → document analysis → AI extraction → validation → vendor verification → duplicate
detection → PO matching → reconciliation → decision → output. The result is one of
**APPROVED**, **NEEDS_REVIEW** or **REJECTED**, with the reason codes, the evidence numbers behind
them, a plain-English explanation, a recommended next action and — where a human needs to chase
someone — a ready-to-send draft email.

## The core design decision

**The LLM reads. Deterministic code decides.**

Gemini is used for exactly one thing: turning a messy PDF into structured, schema-validated fields.
The approve/hold/reject decision is made by a pure-Python rules engine with explicit, configurable
thresholds — no model call inside it. The same invoice and the same reference data produce the same
decision every time, every rule that fires emits a reason code with the numbers it compared, and
the whole thing is unit-tested without touching the network.

That is the answer to "why didn't you just let the model decide?": auditability, reproducibility,
and control over financial risk.

## The interface

| Page | What it does |
|---|---|
| **New run** | Drag-and-drop an invoice PDF, or run any of 14 sample invoices with one click. Each sample says which scenario it exercises. |
| **Live run view** | The ten stages animate `pending → running → result` from real backend events over Server-Sent Events. Expand any stage for its evidence: extracted fields, rules evaluated, PO candidates with scores, the reconciliation table. Alongside it, the original PDF and every field read from it with a confidence score. |
| **Decision card** | Outcome, a plain-English explanation containing the actual figures, the recommended next action, every reason code with evidence, a ready-to-send draft email, and a downloadable audit JSON. |
| **Dashboard** | KPI tiles, full run history, filter by outcome, search by vendor / invoice number / PO. Click any row to replay that run exactly as it happened. |
| **Reference data** | Vendors, purchase orders with live remaining balances, the processed-invoice ledger, the policy file, all reason codes, AI usage in the last 24h, and a "Reset demo data" button. |

Nothing in the UI is simulated: no fake timers, no hardcoded outcomes. Every stage
you see is a real backend event, and the dashboard replays past runs from stored
step records.

## Documentation

| Document | Contents |
|---|---|
| [docs/PROCESS_MAP.md](docs/PROCESS_MAP.md) | Full process flowchart, decision outcomes, tolerance policy, and the complete reason-code reference |
| [ASSUMPTIONS.md](ASSUMPTIONS.md) | Every assumption made where the brief was silent, with rationale |
| docs/DEMO_SCRIPT.md | *(added in the final phase)* 5-minute video script and live-interview run order |

## Running it locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env               # then paste your GEMINI_API_KEY into it
uvicorn backend.app.main:app --reload
```

Open http://127.0.0.1:8000 — reference data seeds itself on first boot into a
local SQLite file. Useful endpoints: `/health`, `/docs`, `/api/samples`,
`/api/reference/purchase-orders`.

```bash
python -m pytest              # full suite, offline, no API calls
python -m pytest -m live      # opt-in: one real Gemini call
python scripts/demo_decision.py   # decision cards, no server needed
```

## Deploying

One service on **Render** (free), one database on **Neon** (free). All state
lives in Postgres, so run history survives redeploys and idle spin-downs —
free hosts have ephemeral filesystems, and anything written to local disk is
lost.

### 1 · Create the database (Neon)

1. Sign up at [neon.tech](https://neon.tech) (GitHub sign-in is fine; no card).
2. **Create project** — name it `zamp-invoice-pipeline`, region **AWS Singapore**
   (`ap-southeast-1`) to sit near the Render region below.
3. On the project dashboard, open **Connection string** and copy the
   **pooled** connection string. It looks like:
   `postgresql://user:password@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

The app rewrites `postgres://` and `postgresql://` to `postgresql+psycopg://`
automatically, so paste the string exactly as Neon gives it.

### 2 · Create the web service (Render)

1. Sign up at [render.com](https://render.com) and connect your GitHub account.
2. **New → Blueprint**, pick this repository. Render reads
   [`render.yaml`](render.yaml) and proposes a free web service in Singapore
   with `/health` as its health check.
3. It will prompt for the two secrets marked `sync: false`:
   - `GEMINI_API_KEY` — your Google AI Studio key
   - `DATABASE_URL` — the Neon pooled connection string from step 1
4. **Apply** and wait for the first build (a few minutes).

If you would rather not use a blueprint: **New → Web Service**, connect the
repo, and set Build Command `pip install -r requirements.txt`, Start Command
`uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`, health check path
`/health`, then add the same environment variables by hand.

### 3 · Verify

```bash
curl https://<your-service>.onrender.com/health
```

Expect `"status":"ok"`, `"database":"ok"` and `"database_kind":"postgres"`.
If that last field says `sqlite`, `DATABASE_URL` never reached the app and run
history would be lost on the next redeploy. Reference data seeds itself on the
first boot.

Then run every scenario against the deployed service:

```bash
python scripts/smoke_live.py --base-url https://<your-service>.onrender.com --reset
```

It wakes the host, checks the database, runs all 14 sample invoices and exits
non-zero if any one of them does not produce its expected outcome.

To restore a known state before a demo:

```bash
curl -X POST https://<your-service>.onrender.com/api/admin/reset
```

### Before a demo — two things, in this order

**1. Reset the demo data.** Reference Data → *Reset demo data*, or:

```bash
curl -X POST https://<your-service>.onrender.com/api/admin/reset
```

This matters more than it looks. Duplicate detection includes a file-hash check,
so running the *same* sample PDF twice without a reset will correctly reject the
second attempt as `DUPLICATE_CONFIRMED_HASH`. That is the system working — but it
is not what you want the happy path to do on camera.

**2. Warm the service.** See below.

### Cold starts — important before a demo

A Render free web service **sleeps after 15 minutes without traffic**, and the
first request afterwards takes roughly 30–60 seconds to wake it. Neon's compute
also suspends after 5 minutes idle and resumes on connect.

**Open the URL two to three minutes before recording or joining the interview**,
wait for `/health` to return `ok`, and everything after that is warm. A paid
Render instance removes the sleep entirely if you prefer not to manage it.

## Status

Under active construction, built in phases. This README is expanded as each phase lands; setup,
deployment and limitations sections arrive with the phases that make them true.

Working end-to-end and deployed. All 14 sample invoices produce their expected
outcome on the live service, after a reset, in order. 248 tests pass offline.

Remaining: the demo script, and a final documentation pass.
