# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SkipTheDemo is a multi-agent AI pipeline that automates demo video creation, design accuracy scoring, and release notes generation from Jira tickets. When QA marks a ticket "Ready for Review," the system fetches the ticket, crawls the staging app with Playwright, records a video, compares screenshots against designs using Claude Vision, generates PM summaries/release notes, and delivers everything to Slack.

## Tech Stack

- **Backend:** FastAPI (Python), async orchestration
- **Browser Automation:** Playwright (async API, headless Chromium)
- **AI:** Anthropic SDK — use `claude-sonnet-4-6` for all Claude calls (vision + synthesis)
- **PDF Parsing:** PyMuPDF (`fitz`)
- **Database:** PostgreSQL via `psycopg2` (raw SQL, no ORM)
- **Messaging:** Slack SDK
- **Frontend:** Lovable (Vite-based), dark theme, purple (#7C3AED) accent

## Development Commands

All commands should be run via `make` targets. Python scripts must run inside the venv.

```bash
# Setup
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Database (reads DATABASE_URL from backend/.env)
make db-reset

# Run backend
make serve

# Test individual agents
make test-jira PROMPT="..."
make test-browser PROMPT="..."
make test-figma PROMPT="..."
make test-slack PROMPT="..."
make test-vision DESIGN=... SCREENSHOT=... PROMPT="..."
make test-synthesis FEATURE="..." PROMPT="..."
```

## Architecture

All Python source lives inside `backend/`. The pipeline is async — `run_pipeline` runs as a background task via `asyncio.create_task`.

**Planning Agent + Deterministic Executor pattern:**

The pipeline uses a two-phase approach instead of an LLM orchestrator loop:

1. **Planning Agent** (`backend/planner.py`) — single-shot Claude call (`claude-sonnet-4-6`) that produces a JSON execution plan and saves it to the `run_steps` DB table. ~1-2K tokens per call.
2. **Deterministic Executor** (`backend/executor.py`) — pure Python async loop that reads the plan from DB and dispatches each step to the appropriate handler. No LLM calls (only through the agents it invokes).
3. **Orchestrator** (`backend/orchestrator.py`) — thin entry point that calls planner → executor → saves results. Also contains `run_browser_pipeline` for standalone browser crawls.

**Pipeline steps** (executed in order by the executor):
1. **jira_fetch** (agent: jira) — fetches ticket, PRD PDF, design attachments, extracts Figma URLs
2. **prd_parse** (agent: internal) — confirms PRD text extraction from downloaded PDFs
3. **figma_export** (agent: figma) — exports design images from Figma links. Skipped if no links found.
4. **browser_crawl** (agent: browser) — Playwright crawls staging URL, records `.webm` video, captures screenshots to `outputs/{job_id}/`
5. **design_compare** (agent: vision) — Claude Vision compares design vs screenshots, returns `{score, deviations, summary}`. Skipped if no design/screenshots.
6. **synthesis** (agent: synthesis) — Claude generates PM summary + release notes
7. **slack_delivery** (agent: slack) — posts briefing message + video to Slack channel

Critical steps (`jira_fetch`, `browser_crawl`) abort the pipeline on failure. Non-critical steps log the error and continue.

Each step updates `run_steps` (step-level tracking via UPSERT — rows created only when steps execute). The LLM-generated plan is stored as JSONB in `runs.plan` (the intent); `run_steps` rows represent reality. `get_plan()` merges both. Frontend polls `GET /status/{job_id}` every 2 seconds.

**API endpoints:** `POST /run`, `POST /run-browser`, `GET /status/{job_id}`, `GET /results/{job_id}`, `GET /plan/{job_id}`, `GET /history`, `GET /history/{job_id}`, `GET /agent-data/{job_id}`, `GET /token-usage/{job_id}`

**Database:** 8 tables — `runs` (job metadata + progress + `plan` JSONB), `run_results` (final outputs as JSONB), `run_steps` (executed step tracking), `run_jira_data`, `run_figma_data`, `run_browser_data` (per-agent data), `run_token_usage` (cost tracking), `run_step_outputs` (per-step outputs as JSONB). All DB functions use context managers with `get_conn()`. `DATABASE_URL` is configured in `backend/.env`.

## Key Conventions

- Always run commands via `make` targets — never run raw `python`, `psql`, etc. directly
- Never run Python scripts outside the virtual environment
- All Claude API calls expect and parse raw JSON responses (no markdown fences)
- Video files are `.webm`, served via FastAPI `StaticFiles` at `/outputs/`
- If no design file is attached, design score = 0, deviations = empty — handle gracefully
- Jira staging URL comes from a custom field configured via `JIRA_STAGING_URL_FIELD` env var
- Frontend env var: `VITE_API_URL` (default `http://localhost:8000`)
- DB credentials and all secrets live in `backend/.env` (loaded via `dotenv` in Python, `include` in Makefile)
- The full technical plan is in `skip-the-demo-plan.md` — refer to it for detailed implementation specs, prompts, and code templates
