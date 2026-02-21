from __future__ import annotations

import os
import uuid

import logging

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log"),
    ],
)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from celery_app import run_pipeline_task
from db.models import create_run, get_dashboard_overview
from routers.runs import router as runs_router

app = FastAPI(title="SkipTheDemo API")
app.include_router(runs_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure outputs directory exists
os.makedirs("outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")


# ── Request / Response models ─────────────


class RunRequest(BaseModel):
    ticket_id: str


class RunResponse(BaseModel):
    job_id: str


# ── Routes ────────────────────────────────


@app.get("/dashboard")
def dashboard():
    return {"runs": get_dashboard_overview()}


@app.post("/run", response_model=RunResponse)
def trigger_run(body: RunRequest):
    job_id = uuid.uuid4().hex[:8]
    create_run(job_id, body.ticket_id)
    try:
        run_pipeline_task.delay(job_id, body.ticket_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not enqueue task (is Redis running?): {exc}",
        )
    return RunResponse(job_id=job_id)
