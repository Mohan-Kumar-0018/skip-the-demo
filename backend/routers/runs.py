from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from db.models import (
    get_browser_data,
    get_dashboard_overview,
    get_figma_data,
    get_jira_data,
    get_plan,
    get_results,
    get_run,
    get_run_steps,
    get_token_usage,
    get_token_usage_summary,
)

router = APIRouter(prefix="/runs", tags=["runs"])

_STEP_DISPLAY_NAMES = {
    "jira_fetch": "Ticket Scout",
    "prd_parse": "Doc Decoder",
    "data_cleanup": "Data Polisher",
    "figma_export": "Design Extractor",
    "discover_crawl": "App Navigator",
    "browser_crawl": "App Navigator",
    "design_compare": "Pixel Judge",
    "demo_video": "Demo Director",
    "synthesis": "Story Weaver",
    "slack_delivery": "Dispatch Runner",
}

# Step name → agent data fetcher
_STEP_AGENT_DATA = {
    "jira_fetch": get_jira_data,
    "figma_export": get_figma_data,
    "discover_crawl": get_browser_data,
    "browser_crawl": get_browser_data,
}


@router.get("/")
def runs_list():
    """All runs with enriched data for the table view."""
    return {"runs": get_dashboard_overview()}


@router.get("/{job_id}")
def run_detail(job_id: str):
    """Single run: header, plan timeline, results, token usage."""
    run = get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Steps from run_steps table (reality, not plan intent)
    steps = get_run_steps(job_id)

    # Build agent_name → cost lookup from token usage
    agent_costs = {}
    for row in get_token_usage(job_id):
        name = row.get("agent_name", "")
        agent_costs[name] = agent_costs.get(name, 0) + (row.get("cost_usd") or 0)

    # step_name → agent_name(s) used in save_token_usage calls
    _STEP_AGENT_NAMES = {
        "jira_fetch": ["jira", "panel_resolver"],
        "figma_export": ["figma"],
        "discover_crawl": ["discover_crawl"],
        "browser_crawl": ["discover_crawl"],
        "design_compare": ["score_evaluator"],
        "demo_video": ["demo_video"],
        "synthesis": ["synthesis"],
        "slack_delivery": ["slack"],
    }

    run_steps = [
        {
            "step_name": s["step_name"],
            "display_name": _STEP_DISPLAY_NAMES.get(s["step_name"], s["step_name"]),
            "agent": s.get("agent"),
            "status": s.get("status"),
            "duration_secs": (
                int((s["completed_at"] - s["started_at"]).total_seconds())
                if s.get("completed_at") and s.get("started_at")
                else None
            ),
            "cost_usd": round(
                sum(agent_costs.get(a, 0) for a in _STEP_AGENT_NAMES.get(s["step_name"], [])),
                4,
            ),
            "error": s.get("error"),
            "result_summary": s.get("result_summary"),
        }
        for s in steps
    ]

    # Results
    results_row = get_results(job_id)
    results = None
    if results_row and results_row.get("design_score") is not None:
        deviations = results_row.get("deviations") or []
        if isinstance(deviations, str):
            deviations = json.loads(deviations)
        screenshots = results_row.get("screenshots") or []
        if isinstance(screenshots, str):
            screenshots = json.loads(screenshots)

        results = {
            "design_score": results_row.get("design_score") or 0,
            "deviations": deviations,
            "summary": results_row.get("summary") or "",
            "release_notes": results_row.get("release_notes") or "",
            "video_url": (
                f"/{results_row['video_path']}"
                if results_row.get("video_path")
                else None
            ),
            "screenshots": [
                f"/{s}" if not s.startswith("/") else s
                for s in screenshots
            ],
            "slack_sent": bool(results_row.get("slack_sent")),
        }

    # Token usage
    agents = [dict(r) for r in get_token_usage(job_id)]
    summary = get_token_usage_summary(job_id)
    token_usage = {
        "agents": agents,
        "totals": dict(summary) if summary else {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0,
        },
    }

    # Duration
    created_at = run.get("created_at")
    completed_at = run.get("completed_at")
    duration_secs = (
        int((completed_at - created_at).total_seconds())
        if completed_at and created_at
        else None
    )

    return {
        "job_id": run["id"],
        "ticket_id": run.get("ticket_id"),
        "feature_name": run.get("feature_name"),
        "status": run.get("status"),
        "created_at": created_at.isoformat() if created_at else None,
        "completed_at": completed_at.isoformat() if completed_at else None,
        "duration_secs": duration_secs,
        "run_steps": run_steps,
        "results": results,
        "token_usage": token_usage,
    }


@router.get("/{job_id}/plan/{step_name}")
def step_detail(job_id: str, step_name: str):
    """Step-level drill-down with plan metadata + relevant agent data."""
    run = get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    plan_steps = get_plan(job_id)
    step = next((s for s in plan_steps if s["step_name"] == step_name), None)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    duration_secs = (
        int((step["completed_at"] - step["started_at"]).total_seconds())
        if step.get("completed_at") and step.get("started_at")
        else None
    )

    # Fetch agent-specific data if a dedicated table exists
    fetcher = _STEP_AGENT_DATA.get(step_name)
    raw = fetcher(job_id) if fetcher else None
    agent_data = dict(raw) if raw else None
    if agent_data:
        for key in ("id", "run_id", "created_at"):
            agent_data.pop(key, None)
        # Normalize screenshot paths to absolute
        if "screenshot_paths" in agent_data:
            paths = agent_data["screenshot_paths"] or []
            if isinstance(paths, str):
                paths = json.loads(paths)
            agent_data["screenshot_paths"] = [
                f"/{p}" if not p.startswith("/") else p for p in paths
            ]

    return {
        "step_name": step["step_name"],
        "agent": step.get("agent"),
        "status": step.get("status"),
        "duration_secs": duration_secs,
        "error": step.get("error"),
        "result_summary": step.get("result_summary"),
        "started_at": (
            step["started_at"].isoformat() if step.get("started_at") else None
        ),
        "completed_at": (
            step["completed_at"].isoformat() if step.get("completed_at") else None
        ),
        "agent_data": agent_data,
    }
