from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db.models import create_run, get_all_runs, get_results, get_run, get_steps
from orchestrator import run_browser_pipeline, run_pipeline

app = FastAPI(title="SkipTheDemo API")

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


class BrowserRunRequest(BaseModel):
    kb_key: str  # e.g. "fina-customer-panel"


class RunResponse(BaseModel):
    job_id: str


# ── Routes ────────────────────────────────


@app.post("/run-browser", response_model=RunResponse)
async def trigger_browser_run(body: BrowserRunRequest):
    job_id = uuid.uuid4().hex[:8]
    create_run(job_id, body.kb_key)
    asyncio.create_task(run_browser_pipeline(job_id, body.kb_key))
    return RunResponse(job_id=job_id)


@app.post("/run", response_model=RunResponse)
async def trigger_run(body: RunRequest):
    job_id = uuid.uuid4().hex[:8]
    create_run(job_id, body.ticket_id)
    asyncio.create_task(run_pipeline(job_id, body.ticket_id))
    return RunResponse(job_id=job_id)


@app.get("/status/{job_id}")
def status(job_id: str):
    run = get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = get_steps(job_id)
    return {**dict(run), "steps": steps}


@app.get("/results/{job_id}")
def results(job_id: str):
    row = get_results(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)
    if result.get("video_path"):
        result["video_url"] = f"/{result['video_path']}"
    return result


@app.get("/history")
def history():
    return {"runs": [dict(r) for r in get_all_runs()]}


@app.get("/history/{job_id}")
def history_detail(job_id: str):
    row = get_results(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)
    if result.get("video_path"):
        result["video_url"] = f"/{result['video_path']}"
    return result
