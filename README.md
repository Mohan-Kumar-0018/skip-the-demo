# SkipTheDemo

Multi-agent AI pipeline that automates demo video creation, design accuracy scoring, and release notes generation from Jira tickets.

When QA marks a ticket "Ready for Review," SkipTheDemo fetches the ticket, crawls the staging app with Playwright, records a video, compares screenshots against designs using Claude Vision, generates PM summaries and release notes, and delivers everything to Slack.

## Architecture

The system uses a **fully agentic architecture** — Claude drives every decision via tool-use loops. The orchestrator is itself a Claude agent that delegates to sub-agents.

```
Orchestrator (Claude agent)
├── Jira Agent      → fetches ticket, PRD, design files, subtasks
├── Browser Agent   → explores staging app, takes screenshots, records video
├── Slack Agent     → posts briefing + uploads video to thread
├── Vision Agent    → compares design vs screenshots (single-shot)
└── Synthesis Agent → generates PM summary + release notes (single-shot)
```

**Key design principle:** `tools/` contains pure API calls (Jira REST, Slack SDK, Playwright). `agents/` contains Claude + system prompt + tool definitions + agentic loop. The orchestrator coordinates agents exposed as tools.

```
backend/
├── main.py              # FastAPI app, routes, static files
├── orchestrator.py      # Master Claude agent — coordinates sub-agents
├── agent_runner.py      # Shared agentic loop (tool-use cycle)
├── agents/
│   ├── jira_agent.py    # Claude agent with Jira tools
│   ├── browser_agent.py # Claude agent with Playwright tools
│   ├── slack_agent.py   # Claude agent with Slack tools
│   ├── vision_agent.py  # Single-shot Claude Vision comparison
│   └── synthesis_agent.py # Single-shot Claude content generation
├── tools/
│   ├── jira_tools.py    # Jira REST API calls
│   ├── slack_tools.py   # Slack SDK calls
│   └── browser_tools.py # Playwright browser automation
├── db/
│   ├── connection.py    # PostgreSQL connection pool
│   ├── models.py        # DB operations (runs, steps, results)
│   └── schema.sql       # Table definitions
└── utils/
    └── pdf_parser.py    # PyMuPDF text extraction
```

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL
- Jira account with API token
- Anthropic API key
- Slack bot token

### 1. Clone and install

```bash
git clone <repo-url>
cd skip-the-demo
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

| Variable | Description |
|---|---|
| `JIRA_HOST` | Jira instance (e.g. `yourcompany.atlassian.net`) |
| `JIRA_EMAIL` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_STAGING_URL_FIELD` | Custom field ID for staging URL |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token |
| `SLACK_CHANNEL` | Target Slack channel (default `#skipdemo-pm`) |
| `DATABASE_URL` | PostgreSQL connection string |

### 3. Set up database

```bash
# Start PostgreSQL (if using Docker)
docker run --name skipdemo-db -e POSTGRES_DB=skipdemo -p 5432:5432 -d postgres

# Create tables (reads DATABASE_URL from backend/.env)
make db-reset
```

### 4. Run

```bash
cd backend
uvicorn main:app --reload --port 8000
```

## API

### `POST /run`

Trigger a pipeline run.

```bash
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id": "PROJ-123"}'
```

Returns `{"job_id": "a1b2c3d4"}`.

### `GET /status/{job_id}`

Poll pipeline progress. Returns current stage, progress percentage, and per-step statuses.

### `GET /results/{job_id}`

Fetch final results: design score, deviations, summary, release notes, video URL.

### `GET /history`

List all pipeline runs.

### `GET /history/{job_id}`

Get detailed results for a specific run.

## How It Works

1. **POST /run** creates a job and kicks off the orchestrator as a background task
2. The **orchestrator** (Claude agent) plans and executes the pipeline:
   - Calls the **Jira agent** to fetch the ticket, download PRD and design attachments
   - Calls the **Browser agent** to navigate the staging URL, explore flows, capture screenshots, and record a `.webm` video
   - Calls **analyze_design** to compare the design file against screenshots using Claude Vision — produces a score (0-100) and list of deviations
   - Calls **generate_content** to write a PM summary and user-facing release notes
   - Calls the **Slack agent** to post the briefing and upload the video
3. Each step updates progress in the database — the frontend polls `/status/{job_id}` every 2 seconds
4. Results are saved to the `run_results` table and served via `/results/{job_id}`

## Tech Stack

- **Backend:** FastAPI, async Python
- **AI:** Anthropic SDK (`claude-sonnet-4-6`) — tool-use agentic loops
- **Browser Automation:** Playwright (headless Chromium)
- **PDF Parsing:** PyMuPDF
- **Database:** PostgreSQL (raw SQL, no ORM)
- **Messaging:** Slack SDK
