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

All Python source lives inside `backend/`. The pipeline is async — `run_pipeline` is dispatched to a Celery worker via Redis and runs inside `asyncio.run()`.

**Planning Agent + Deterministic Executor pattern:**

The pipeline uses a two-phase approach instead of an LLM orchestrator loop:

1. **Planning Agent** (`backend/planner.py`) — single-shot Claude call (`claude-sonnet-4-6`) that produces a JSON execution plan and saves it to the `run_steps` DB table. ~1-2K tokens per call.
2. **Deterministic Executor** (`backend/executor.py`) — pure Python async loop that reads the plan from DB and dispatches each step to the appropriate handler. No LLM calls (only through the agents it invokes).
3. **Orchestrator** (`backend/orchestrator.py`) — thin entry point that calls planner → executor → saves results. Also contains `run_browser_pipeline` for standalone browser crawls.
4. **Celery Tasks** (`backend/celery_app.py`) — thin wrappers that call `asyncio.run()` around the async pipeline functions. Workers are independent processes backed by Redis.

**Pipeline Agents:**

| Display Name | Agent | File | Step | Critical | Role |
|---|---|---|---|---|---|
| Ticket Scout | Jira Agent | `agents/jira_agent.py` | `jira_fetch` | Yes | Fetches Jira ticket details, downloads attachments (PRDs, designs), extracts Figma URLs from description/comments, collects subtasks and comments. |
| Doc Decoder | PRD Parser | `executor.py` (internal) | `prd_parse` | No | Extracts text content from downloaded PRD PDF attachments using PyMuPDF. No LLM call. |
| Data Polisher | Data Cleanup | `executor.py` (internal) | `data_cleanup` | No | Internal cleanup/normalization of extracted data between fetch and export phases. |
| Design Extractor | Figma Agent | `agents/figma_agent.py` | `figma_export` | No | Parses Figma URLs, fetches file/node metadata from Figma API, batch-exports design frames as 2x PNGs. Skipped if no Figma links. |
| App Navigator | Discover-Crawl Agent | `agents/discover_crawl_agent.py` | `discover_crawl` | Yes | 3-phase browser automation: deterministic login → nav discovery (optionally guided by Figma designs) → structured crawl. Records video, captures screenshots. |
| Pixel Judge | Score Evaluator | `agents/score_evaluator_agent.py` | `design_compare` | No | Multi-phase Claude Vision comparison (5-7 API calls): inventories Figma screens, inventories UAT screenshots, matches them, scores visual fidelity 0-100, lists deviations. Skipped if no designs/screenshots. |
| Demo Director | Demo Video Agent | `agents/demo_video_agent.py` | `demo_video` | No | Post-processes raw .webm recordings: frame deduplication, click ripple animations (purple #7C3AED accent), AI-generated narration via edge-tts + subtitle overlays. |
| Story Weaver | Synthesis Agent | `agents/synthesis_agent.py` | `synthesis` | No | Single-shot Claude call generating a PM summary (3-4 sentences) and user-facing release notes in markdown. |
| Dispatch Runner | Slack Agent | `agents/slack_agent.py` | `slack_delivery` | No | Posts briefing message to Slack with score emoji indicators (green/yellow/red), uploads demo video to thread. |

**Internal helper agents** (not pipeline steps):
- **Navigation Planner** (`agents/navigation_planner_agent.py`) — analyzes Figma screenshots to identify distinct screens and navigation flow. Used internally by Discover-Crawl.
- **Panel Resolver** (inside `executor.py`) — lightweight Claude call (~50 tokens) to determine which staging app KB panel a ticket maps to. Runs after jira_fetch + figma_export.

**Pipeline flow:**
```
Ticket Scout → Doc Decoder → Design Extractor → App Navigator → Pixel Judge → Demo Director → Story Weaver → Dispatch Runner
  (critical)                    (skippable)       (critical)     (skippable)    (skippable)
```

Critical steps abort the pipeline on failure. Non-critical steps log the error and continue.

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
