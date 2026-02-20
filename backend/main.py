from __future__ import annotations

import asyncio
import io
import json
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fpdf import FPDF
from pydantic import BaseModel

from db.models import (
    create_run,
    get_all_runs,
    get_browser_data,
    get_dashboard_stats,
    get_figma_data,
    get_jira_data,
    get_plan,
    get_results,
    get_run,
    get_steps,
    get_token_usage,
    get_token_usage_summary,
)
from orchestrator import run_browser_pipeline, run_pipeline
from routers.explorer import router as explorer_router

app = FastAPI(title="SkipTheDemo API")
app.include_router(explorer_router)

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
    steps_raw = get_steps(job_id)
    steps = [{"name": name, "status": status} for name, status in steps_raw.items()]
    return {
        "job_id": run["id"],
        "ticket_id": run["ticket_id"],
        "status": run["status"],
        "progress": run["progress"],
        "current_action": run["stage"],
        "steps": steps,
    }


@app.get("/results/{job_id}")
def results(job_id: str):
    row = get_results(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)

    # Parse JSON fields stored as strings
    deviations = result.get("deviations") or []
    if isinstance(deviations, str):
        deviations = json.loads(deviations)

    screenshots = result.get("screenshots") or []
    if isinstance(screenshots, str):
        screenshots = json.loads(screenshots)

    # Fetch related data for stats computation
    browser = get_browser_data(job_id)
    jira = get_jira_data(job_id)

    urls_visited = []
    if browser:
        urls_visited = browser.get("urls_visited") or []
        if isinstance(urls_visited, str):
            urls_visited = json.loads(urls_visited)

    jira_attachments = []
    if jira:
        jira_attachments = jira.get("attachments") or []
        if isinstance(jira_attachments, str):
            jira_attachments = json.loads(jira_attachments)

    # Compute stats
    stats = {
        "videos_generated": 1 if result.get("video_path") else 0,
        "pdfs_generated": len(jira_attachments) if jira_attachments else (1 if result.get("release_notes") else 0),
        "jiras_crawled": 1 if jira else 0,
        "screenshots_taken": len(screenshots),
        "pages_explored": len(urls_visited),
    }

    completed_at = result.get("completed_at")
    if completed_at and hasattr(completed_at, "isoformat"):
        completed_at = completed_at.isoformat()

    return {
        "job_id": result["id"],
        "ticket_id": result.get("ticket_id", ""),
        "feature_name": result.get("feature_name", ""),
        "score": result.get("design_score") or 0,
        "deviations": deviations,
        "pm_summary": result.get("summary", ""),
        "release_notes": result.get("release_notes", ""),
        "video_url": f"/{result['video_path']}" if result.get("video_path") else "",
        "pdf_url": f"/results/{job_id}/release-notes.pdf",
        "screenshots_pdf_url": f"/results/{job_id}/screenshots.pdf",
        "briefing_sent": bool(result.get("slack_sent")),
        "completed_at": completed_at or "",
        "stats": stats,
    }


@app.get("/results/{job_id}/release-notes.pdf")
def release_notes_pdf(job_id: str):
    row = get_results(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)
    release_notes = result.get("release_notes", "") or ""
    feature_name = result.get("feature_name", "Release Notes") or "Release Notes"

    def _safe(text: str) -> str:
        """Strip markdown bold markers and replace unicode chars unsupported by Helvetica."""
        return (
            text.replace("**", "")
            .replace("\u2014", "--")   # em-dash
            .replace("\u2013", "-")    # en-dash
            .replace("\u2018", "'")    # left single quote
            .replace("\u2019", "'")    # right single quote
            .replace("\u201c", '"')    # left double quote
            .replace("\u201d", '"')    # right double quote
            .replace("\u2022", "-")    # bullet
        )

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, _safe(feature_name), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 11)

    for line in release_notes.split("\n"):
        stripped = line.strip()
        if stripped.startswith("### "):
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(0, 8, _safe(stripped[4:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 11)
        elif stripped.startswith("## "):
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 9, _safe(stripped[3:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 11)
        elif stripped.startswith("# "):
            pdf.ln(5)
            pdf.set_font("Helvetica", "B", 16)
            pdf.cell(0, 10, _safe(stripped[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 11)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.cell(5)
            pdf.cell(0, 7, f"-  {_safe(stripped[2:])}", new_x="LMARGIN", new_y="NEXT")
        elif stripped.startswith("---"):
            pdf.ln(2)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + pdf.epw, pdf.get_y())
            pdf.ln(2)
        elif stripped == "":
            pdf.ln(3)
        else:
            pdf.multi_cell(0, 7, _safe(stripped))

    buf = io.BytesIO(pdf.output())
    safe_name = feature_name.replace(" ", "-").lower()
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-release-notes.pdf"'},
    )


@app.get("/results/{job_id}/screenshots.pdf")
def screenshots_pdf(job_id: str):
    row = get_results(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)
    feature_name = result.get("feature_name", "Screenshots") or "Screenshots"

    screenshots = result.get("screenshots") or []
    if isinstance(screenshots, str):
        screenshots = json.loads(screenshots)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    if not screenshots:
        pdf.add_page()
        pdf.set_font("Helvetica", "", 14)
        pdf.cell(0, 10, "No screenshots available for this run.", align="C")
    else:
        for i, screenshot_path in enumerate(screenshots):
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 10, f"Screenshot {i + 1} of {len(screenshots)}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            # screenshot_path is relative to the backend working directory
            abs_path = os.path.join(os.getcwd(), screenshot_path) if not os.path.isabs(screenshot_path) else screenshot_path
            if os.path.exists(abs_path):
                try:
                    pdf.image(abs_path, x=10, w=pdf.epw)
                except Exception:
                    pdf.set_font("Helvetica", "I", 10)
                    pdf.cell(0, 8, f"Could not embed image: {screenshot_path}", new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_font("Helvetica", "I", 10)
                pdf.cell(0, 8, f"File not found: {screenshot_path}", new_x="LMARGIN", new_y="NEXT")

    buf = io.BytesIO(pdf.output())
    safe_name = feature_name.replace(" ", "-").lower()
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-screenshots.pdf"'},
    )


@app.get("/history")
def history():
    runs_raw = get_all_runs()
    runs = [
        {
            "job_id": r["id"],
            "ticket_id": r["ticket_id"],
            "feature_name": r["feature_name"],
            "score": r["design_score"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in runs_raw
    ]
    return {"runs": runs}


@app.get("/history/{job_id}")
def history_detail(job_id: str):
    row = get_results(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)
    if result.get("video_path"):
        result["video_url"] = f"/{result['video_path']}"
    return result


@app.get("/dashboard")
def dashboard():
    stats = get_dashboard_stats()
    return {"aggregate_stats": dict(stats)}


@app.get("/agent-data/{job_id}")
def agent_data(job_id: str):
    run = get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    jira = get_jira_data(job_id)
    figma = get_figma_data(job_id)
    browser = get_browser_data(job_id)
    return {
        "jira": dict(jira) if jira else None,
        "figma": dict(figma) if figma else None,
        "browser": dict(browser) if browser else None,
    }


@app.get("/plan/{job_id}")
def plan(job_id: str):
    run = get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = get_plan(job_id)
    return {"run_id": job_id, "steps": steps}


@app.get("/token-usage/{job_id}")
def token_usage(job_id: str):
    run = get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    rows = get_token_usage(job_id)
    summary = get_token_usage_summary(job_id)
    return {
        "agents": [dict(r) for r in rows],
        "totals": dict(summary) if summary else {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0,
        },
    }
