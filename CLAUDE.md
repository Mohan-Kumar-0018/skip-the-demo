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

```bash
# Setup
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Database
docker run --name skipdemo-db -e POSTGRES_PASSWORD=skipdemo -e POSTGRES_DB=skipdemo -p 5432:5432 -d postgres
psql postgresql://postgres:skipdemo@localhost:5432/skipdemo -f db/schema.sql

# Run backend
uvicorn main:app --reload --port 8000
```

## Architecture

All Python source lives inside `backend/`. The pipeline is async — `run_pipeline` runs as a background task via `asyncio.create_task`.

**Orchestrator pattern:** `backend/orchestrator.py` is the master controller that coordinates agents sequentially:
1. **Jira Agent** (`agents/jira_agent.py`) — fetches ticket, PRD PDF, and design attachments via REST API
2. **PDF Parser** (`utils/pdf_parser.py`) — extracts text from PRD using PyMuPDF
3. **Browser Agent** (`agents/browser_agent.py`) — Playwright crawls staging URL, records `.webm` video, captures screenshots to `outputs/{job_id}/`
4. **Vision Agent** (`agents/vision_agent.py`) — Claude Vision compares design vs screenshots, returns `{score, deviations, summary}`
5. **Synthesis Agent** (`agents/synthesis_agent.py`) — Claude generates PM summary + release notes, returns `{summary, release_notes}`
6. **Slack Agent** (`agents/slack_agent.py`) — posts briefing message + video to Slack channel

Each step updates the DB with status/progress. Frontend polls `GET /status/{job_id}` every 2 seconds.

**API endpoints:** `POST /run`, `GET /status/{job_id}`, `GET /results/{job_id}`, `GET /history`, `GET /history/{job_id}`

**Database:** 3 tables — `runs` (job metadata + progress), `run_steps` (per-step status), `run_results` (final outputs as JSONB). All DB functions use context managers with `get_conn()`.

## Key Conventions

- All Claude API calls expect and parse raw JSON responses (no markdown fences)
- Video files are `.webm`, served via FastAPI `StaticFiles` at `/outputs/`
- If no design file is attached, design score = 0, deviations = empty — handle gracefully
- Jira staging URL comes from a custom field configured via `JIRA_STAGING_URL_FIELD` env var
- Frontend env var: `VITE_API_URL` (default `http://localhost:8000`)
- The full technical plan is in `skip-the-demo-plan.md` — refer to it for detailed implementation specs, prompts, and code templates
