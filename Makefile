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

## Discover-Crawl Agent — login, discover nav, full crawl using KB credentials
## Example: make test-discover KB_KEY=fina-customer-panel
## Example with Figma: make test-discover KB_KEY=fina-customer-panel FIGMA_DIR=outputs/23d8c274
test-discover:
	$(ACTIVATE) && KB_KEY="$(KB_KEY)" FIGMA_DIR="$(FIGMA_DIR)" python test_agent.py discover_crawl "auto"

# ─── Single-shot agents (prompt + env vars for files) ────────

## Vision Agent — compare design image vs screenshot
## Requires: DESIGN (path to design image), SCREENSHOT (path to screenshot)
## Example: make test-vision DESIGN=outputs/test-SG-238/design.png SCREENSHOT=outputs/test-SG-238/screen_1.png PROMPT="compare"
test-vision:
	$(ACTIVATE) && DESIGN="$(DESIGN)" SCREENSHOT="$(SCREENSHOT)" python test_agent.py vision "$(PROMPT)"

## Navigation Planner — analyze Figma design PNGs to produce navigation flow
## Requires: IMAGES_DIR (directory with design PNGs), PROMPT (PRD text / context)
## Example: make test-nav IMAGES_DIR=outputs/23d8c274 PROMPT="Supplier discovery feature"
test-nav:
	$(ACTIVATE) && IMAGES_DIR="$(IMAGES_DIR)" python test_agent.py nav_planner "$(PROMPT)"

## Score Evaluator — compare UAT screenshots against Figma designs (multi-phase)
## Requires: UAT_DIR (directory with UAT PNGs), FIGMA_DIR (directory with Figma PNGs)
## Example: make test-score-eval UAT_DIR=outputs/uat_screenshots FIGMA_DIR=outputs/23d8c274
test-score-eval:
	$(ACTIVATE) && UAT_DIR="$(UAT_DIR)" FIGMA_DIR="$(FIGMA_DIR)" python test_agent.py score_eval "evaluate"

## Demo Video Agent — post-process raw .webm/.mov into polished demo .mp4
## Requires: VIDEO (path to .webm or .mov), ACTION_LOG (path to action_log.json)
## Optional: SCREENSHOTS_DIR (directory with PNGs), FEATURE (feature description)
## Example: make test-demo-video VIDEO=outputs/uat_screenshots/abc.webm ACTION_LOG=outputs/uat_screenshots/action_log.json
## Example: make test-demo-video VIDEO=path/to/recording.mov ACTION_LOG=outputs/uat_screenshots/action_log.json
test-demo-video:
	$(ACTIVATE) && VIDEO="$(VIDEO)" ACTION_LOG="$(ACTION_LOG)" SCREENSHOTS_DIR="$(SCREENSHOTS_DIR)" FEATURE="$(FEATURE)" python test_agent.py demo_video "generate"

## Synthesis Agent — generate PM summary + release notes from PRD text
## Optional: FEATURE (feature name), DESIGN_RESULT (JSON string with score/deviations)
## Example: make test-synthesis FEATURE="Supplier Discovery" PROMPT="This feature allows users to search and filter suppliers..."
test-synthesis:
	$(ACTIVATE) && FEATURE="$(FEATURE)" DESIGN_RESULT='$(DESIGN_RESULT)' python test_agent.py synthesis "$(PROMPT)"

# ─── Utilities ───────────────────────────────────────────────

## Trigger a full pipeline run for a Jira ticket
## Example: make run TICKET=SG-238
run:
	curl -s -X POST http://localhost:8000/run -H 'Content-Type: application/json' -d '{"ticket_id":"$(TICKET)"}' | python -m json.tool

## Reset DB — drop all tables and recreate from schema, clear outputs
db-reset:
	psql $(DB_URL) -c "DROP TABLE IF EXISTS run_step_outputs, run_plan, run_token_usage, run_browser_data, run_figma_data, run_jira_data, run_results, run_steps, runs CASCADE;"
	psql $(DB_URL) -f backend/db/schema.sql
	rm -rf backend/outputs/*/
	rm -f backend/pipeline.log

## Install Python deps + Playwright browser
install:
	$(ACTIVATE) && pip install -r requirements.txt && playwright install chromium

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
	@echo "  make test-discover   KB_KEY=... [FIGMA_DIR=...]        Discover-crawl (login + nav + crawl)"
	@echo "  make test-slack      PROMPT=\"...\"                       Slack agent (post messages, upload files)"
	@echo "  make test-vision     DESIGN=... SCREENSHOT=... PROMPT=  Vision agent (design vs screenshot)"
	@echo "  make test-figma      PROMPT=\"...\"                       Figma agent (extract design images)"
	@echo "  make test-nav        IMAGES_DIR=... PROMPT=\"...\"        Nav planner (design screens → nav flow)"
	@echo "  make test-score-eval UAT_DIR=... FIGMA_DIR=...          Score evaluator (design vs UAT multi-phase)"
	@echo "  make test-demo-video VIDEO=... ACTION_LOG=...          Demo video generator (raw .webm/.mov → polished .mp4)"
	@echo "  make test-synthesis  PROMPT=\"...\" FEATURE=\"...\"         Synthesis agent (PM summary + release notes)"
	@echo ""
	@echo "  make run             TICKET=SG-238                      Trigger full pipeline run"
	@echo "  make install                                            Install Python deps + Playwright"
	@echo "  make serve                                              Start FastAPI backend on :8000"
	@echo "  make db-reset                                           Drop all tables and recreate from schema"
	@echo ""
