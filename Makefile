SHELL := /bin/bash

ACTIVATE := cd backend && source ../venv/bin/activate

# Load DATABASE_URL from backend/.env, allow override via environment
include backend/.env
export DATABASE_URL
DB_URL := $(DATABASE_URL)

# ─── Agentic agents (plain text prompt) ──────────────────────

## Jira Agent — fetch tickets, subtasks, attachments
## Example: make test-jira PROMPT="Fetch all details for ticket SG-238 including subtasks and attachments. Save attachments to outputs/test-SG-238."
test-jira:
	$(ACTIVATE) && python test_agent.py jira "$(PROMPT)"

## Browser Agent — crawl a URL, take screenshots, record video
## Example: make test-browser PROMPT="Navigate to https://example.com with job_id test-001. Take screenshots and explore the page."
test-browser:
	$(ACTIVATE) && python test_agent.py browser "$(PROMPT)"

## Browser Agent (auto-discovery) — explore a specific page using KB credentials
## Example: make test-browser-page KB_KEY=fina-customer-panel PAGE=Suppliers
test-browser-page:
	$(ACTIVATE) && KB_KEY="$(KB_KEY)" PAGE="$(PAGE)" python test_agent.py browser "auto"

## Slack Agent — post messages, upload files to Slack
## Example: make test-slack PROMPT="Post a message to #skipdemo-pm saying: Hello from SkipTheDemo test run!"
test-slack:
	$(ACTIVATE) && python test_agent.py slack "$(PROMPT)"

## Figma Agent — extract design images from Figma links
## Example: make test-figma PROMPT="Extract the design from this Figma link: https://www.figma.com/design/KEY/Title?node-id=13-1134. Save to outputs/test-figma/"
test-figma:
	$(ACTIVATE) && python test_agent.py figma "$(PROMPT)"

# ─── Single-shot agents (prompt + env vars for files) ────────

## Vision Agent — compare design image vs screenshot
## Requires: DESIGN (path to design image), SCREENSHOT (path to screenshot)
## Example: make test-vision DESIGN=outputs/test-SG-238/design.png SCREENSHOT=outputs/test-SG-238/screen_1.png PROMPT="compare"
test-vision:
	$(ACTIVATE) && DESIGN="$(DESIGN)" SCREENSHOT="$(SCREENSHOT)" python test_agent.py vision "$(PROMPT)"

## Synthesis Agent — generate PM summary + release notes from PRD text
## Optional: FEATURE (feature name), DESIGN_RESULT (JSON string with score/deviations)
## Example: make test-synthesis FEATURE="Supplier Discovery" PROMPT="This feature allows users to search and filter suppliers..."
test-synthesis:
	$(ACTIVATE) && FEATURE="$(FEATURE)" DESIGN_RESULT='$(DESIGN_RESULT)' python test_agent.py synthesis "$(PROMPT)"

# ─── Utilities ───────────────────────────────────────────────

## Reset DB — drop all tables and recreate from schema
db-reset:
	psql $(DB_URL) -c "DROP TABLE IF EXISTS run_plan, run_token_usage, run_browser_data, run_figma_data, run_jira_data, run_results, run_steps, runs CASCADE;"
	psql $(DB_URL) -f backend/db/schema.sql

## Run the full backend server
serve:
	$(ACTIVATE) && uvicorn main:app --reload --port 8000

## List available agents
help:
	@echo ""
	@echo "SkipTheDemo — Agent Test Commands"
	@echo "================================="
	@echo ""
	@echo "  make test-jira       PROMPT=\"...\"                       Jira agent (tickets, subtasks, attachments)"
	@echo "  make test-browser    PROMPT=\"...\"                       Browser agent (crawl, screenshot, record)"
	@echo "  make test-browser-page KB_KEY=... PAGE=...              Browser auto-discovery (KB credentials)"
	@echo "  make test-slack      PROMPT=\"...\"                       Slack agent (post messages, upload files)"
	@echo "  make test-vision     DESIGN=... SCREENSHOT=... PROMPT=  Vision agent (design vs screenshot)"
	@echo "  make test-figma      PROMPT=\"...\"                       Figma agent (extract design images)"
	@echo "  make test-synthesis  PROMPT=\"...\" FEATURE=\"...\"         Synthesis agent (PM summary + release notes)"
	@echo ""
	@echo "  make serve                                              Start FastAPI backend on :8000"
	@echo "  make db-reset                                           Drop all tables and recreate from schema"
	@echo ""
