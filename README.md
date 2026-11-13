# AI Invoice Processing Pipeline

From invoice PDF to a reasoned, auditable AP decision — with every step visible.

Built for the Zamp AI Solutions Analyst case study (**PS-1 · Finance / AP**).

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
Reference data seeds itself on the first boot.

To restore a known state before a demo:

```bash
curl -X POST https://<your-service>.onrender.com/api/admin/reset
```

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

Phases 1-4 complete: process map and assumptions, reference data and sample invoices,
the deterministic decision engine (234 tests), and Gemini extraction for both text
and scanned PDFs. The pipeline orchestration, live run view and dashboard are next.
