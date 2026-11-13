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

## Status

Under active construction, built in phases. This README is expanded as each phase lands; setup,
deployment and limitations sections arrive with the phases that make them true.

**Phase 1 complete** — process map, assumptions, repository skeleton.
