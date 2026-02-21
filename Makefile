SHELL := /bin/bash

ACTIVATE := cd backend && source ../venv/bin/activate

# Load DATABASE_URL from backend/.env, allow override via environment
include backend/.env
export DATABASE_URL
export REDIS_URL
DB_URL := $(DATABASE_URL)

# ─── Utilities ───────────────────────────────────────────────

## Trigger a full pipeline run for a Jira ticket
## Example: make run TICKET=SG-238
run:
	curl -s -X POST http://localhost:8000/run -H 'Content-Type: application/json' -d '{"ticket_id":"$(TICKET)"}' | python -m json.tool

## Reset DB — drop all tables and recreate from schema, clear outputs
db-reset:
	psql $(DB_URL) -c "DROP TABLE IF EXISTS run_step_outputs, run_steps, run_token_usage, run_browser_data, run_figma_data, run_jira_data, run_results, runs CASCADE;"
	psql $(DB_URL) -f backend/db/schema.sql
	rm -rf backend/outputs/*/
	> backend/pipeline.log

## Add new columns to existing DB (non-destructive)
db-migrate:
	psql $(DB_URL) -c "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS ai_summary TEXT;"

## Install Python deps + Playwright browser
install:
	$(ACTIVATE) && pip install -r ../requirements.txt && playwright install chromium

## Run the full backend server
serve:
	$(ACTIVATE) && uvicorn main:app --reload --port 8000

## Start 1 Celery worker (concurrency=1, prefork pool)
worker:
	$(ACTIVATE) && celery -A celery_app worker --pool=prefork --concurrency=1 -Q pipeline --loglevel=info

## Start N Celery workers: make worker-scale N=3
N ?= 2
worker-scale:
	$(ACTIVATE) && celery -A celery_app worker --pool=prefork --concurrency=$(N) -Q pipeline --loglevel=info

## Flower monitoring UI on :5555
flower:
	$(ACTIVATE) && celery -A celery_app flower --port=5555

## Check active Celery tasks
worker-status:
	$(ACTIVATE) && celery -A celery_app inspect active

## Clear all pending tasks from the queue
worker-purge:
	$(ACTIVATE) && celery -A celery_app purge -f

## Show available commands
help:
	@echo ""
	@echo "SkipTheDemo"
	@echo "==========="
	@echo ""
	@echo "  make run           TICKET=SG-238  Trigger full pipeline run"
	@echo "  make db-reset                     Drop all tables and recreate from schema"
	@echo "  make install                      Install Python deps + Playwright"
	@echo "  make serve                        Start FastAPI backend on :8000"
	@echo "  make worker                       Start 1 Celery worker"
	@echo "  make worker-scale  N=3            Start N concurrent workers"
	@echo "  make flower                       Flower monitoring UI on :5555"
	@echo "  make worker-status                Check active Celery tasks"
	@echo "  make worker-purge                 Clear pending task queue"
	@echo ""
