"""Celery app + thin task wrappers around async pipeline functions.

Each task calls asyncio.run() so the existing async code (scheduler,
executor, agents, Playwright) runs untouched inside the worker's own
event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure backend/ is on sys.path so forked workers can resolve imports
# (orchestrator, db.*, agents.*, etc.) regardless of cwd.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery import Celery
from celery.signals import worker_process_init
from dotenv import load_dotenv

load_dotenv(os.path.join(_backend_dir, ".env"))

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("skipdemo", broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    # Long-running tasks — don't let a single worker hoard messages
    worker_prefetch_multiplier=1,
    # Requeue if worker crashes mid-task
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # 30 min soft limit per task
    task_soft_time_limit=1800,
    # JSON serializer (all args are simple strings)
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Route all pipeline tasks to the "pipeline" queue
    task_default_queue="pipeline",
)


def _ensure_backend_path():
    """Ensure backend/ is on sys.path and is the cwd.

    Called in worker_process_init (after fork) and at the top of each
    task so imports like ``from orchestrator import ...`` always resolve.
    """
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
    os.chdir(_backend_dir)


@worker_process_init.connect
def _on_worker_init(**kwargs):
    """Post-fork setup: fix sys.path and reset the DB connection pool.

    psycopg2 connections aren't safe across fork boundaries, so we
    discard the parent's pool and let each worker build its own lazily.
    """
    _ensure_backend_path()
    try:
        import db.connection

        if db.connection._pool is not None:
            db.connection._pool = None
            logger.info("DB connection pool reset after fork")
    except Exception:
        pass


# ── Task wrappers ──────────────────────────────────────────────


@app.task(name="pipeline.run", bind=True, max_retries=0)
def run_pipeline_task(self, run_id: str, ticket_id: str):
    """Full pipeline: plan → execute → deliver."""
    _ensure_backend_path()
    from orchestrator import run_pipeline

    logger.info("Celery task started: run_pipeline(%s, %s)", run_id, ticket_id)
    asyncio.run(run_pipeline(run_id, ticket_id))


@app.task(name="pipeline.run_browser", bind=True, max_retries=0)
def run_browser_pipeline_task(self, run_id: str, kb_key: str):
    """Standalone browser crawl."""
    _ensure_backend_path()
    from orchestrator import run_browser_pipeline

    logger.info("Celery task started: run_browser_pipeline(%s, %s)", run_id, kb_key)
    asyncio.run(run_browser_pipeline(run_id, kb_key))


@app.task(
    name="pipeline.run_discover_crawl", bind=True, max_retries=0
)
def run_discover_crawl_pipeline_task(
    self, run_id: str, kb_key: str, figma_images_dir: str | None = None
):
    """Discover-crawl pipeline."""
    _ensure_backend_path()
    from orchestrator import run_discover_crawl_pipeline

    logger.info(
        "Celery task started: run_discover_crawl_pipeline(%s, %s)", run_id, kb_key
    )
    asyncio.run(run_discover_crawl_pipeline(run_id, kb_key, figma_images_dir))
