# ITR Assist

Automated **Income Tax Return** filing assistant for **salaried individuals** in India
(AY 2026-27 / FY 2025-26). It interviews you, builds a document checklist, reads your
documents with AI document intelligence, runs a deterministic and independently verified
tax computation (old vs new regime), and gives a guided, copy-paste filing walkthrough that
mirrors the income tax e-filing portal.

> Disclaimer: This is not tax advice. All figures are best-effort and must be verified on
> the official portal (incometax.gov.in) before filing.

## What it does

- **Questionnaire** picks the right form (ITR-1 vs ITR-2) with a deterministic rule engine.
- **Document checklist** with step-by-step "how to fetch" instructions.
- **Live document intelligence** (OpenAI): extracts every field with confidence scores,
  a self-critique feedback loop, and provenance.
- **Cross-source reconciliation** (Form 16 vs 26AS vs AIS vs broker P&L).
- **Deterministic tax engine** (pure Python) compares old vs new regime; an **independent
  re-computation** verifies the result. LLMs never do the math.
- **Guided filing** walkthrough, schedule-by-schedule, with copy buttons.
- **Help chatbot** for ITR / portal / website questions.
- **Real-time UI**: live extraction view, computation waterfall, regime comparison, and an
  agent activity timeline streamed over SSE.

## Architecture

```
backend/   FastAPI + Google ADK 2.0 agents (OpenAI via LiteLLM)
  app/       API routes, SSE event bus, SQLite session store, config, logging
  agents/    intake, doc_intel (extraction), reconcile, guidance, chat, orchestrator
  compute/   deterministic engine, FY2025-26 constants, validators, re-compute
  schemas/   Pydantic models
  tests/     unit tests for engine + validators
frontend/  Next.js (App Router, TypeScript) wizard UI + live panels
```

## Prerequisites

- **Python 3.14**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (Python package manager)
- **Node.js 18+** (tested on Node 25)
- An **OpenAI API key**

Install `uv` if not already present: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Setup

### 1. Backend

```bash
# from the repo root
uv sync
```

Create a `.env` in the repo root (see `.env.example`):

```bash
cp .env.example .env
# then edit .env and set OPENAI_API_KEY=sk-...
```

All model choices and settings are configurable in `.env` (extraction/validation use
`gpt-5`; lighter prose and chat use `gpt-5-mini`).

### 2. Frontend

```bash
cd frontend
npm install
```

## Run locally

Open two terminals.

**Terminal 1 - backend (port 8000):**

```bash
uv run uvicorn backend.app.main:app --port 8000
```

**Terminal 2 - frontend (port 3000):**

```bash
cd frontend
npm run dev
```

Then open **http://localhost:3000**.

The frontend talks to the backend at `http://127.0.0.1:8000` by default. To change it, set
`NEXT_PUBLIC_API_BASE` before `npm run dev`.

## Tests

```bash
# unit tests (no API key needed)
uv run pytest backend/tests

# live end-to-end smoke test (backend running + OPENAI_API_KEY set)
uv run python -m backend.debug.e2e_live
```

## Logs

Structured JSON logs (with per-session correlation ids and full agent/event traces) are
written to `backend/_data/logs/backend.jsonl`.

## Scope

- Covers ITR-1 and ITR-2 salaried scenarios: job changes, multiple Form 16s, house property,
  capital gains (STCG/LTCG/112A/VDA), dividends, interest, PF/NPS, and 80C/80D/24(b) deductions.
- Out of scope: direct auto-submit to the portal (no public prefill API), ITR-3/4 / business
  income, and automated e-verification.
