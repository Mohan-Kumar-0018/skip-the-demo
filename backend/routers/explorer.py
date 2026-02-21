from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from db.models import (
    get_run_by_id,
    get_run_result_by_id,
    get_run_jira_data_by_id,
    get_run_figma_data_by_id,
    get_run_browser_data_by_id,
    get_run_token_usage_by_id,
    get_run_plan_by_id,
    list_run_plan,
)

router = APIRouter(prefix="/api/explorer", tags=["explorer"])


# ── Runs ─────────────────────────────────


@router.get("/runs/{run_id}")
def api_get_run(run_id: str):
    row = get_run_by_id(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


# ── Run Results ──────────────────────────


@router.get("/run-results/{result_id}")
def api_get_run_result(result_id: int):
    row = get_run_result_by_id(result_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run result not found")
    return row


# ── Run Jira Data ────────────────────────


@router.get("/run-jira-data/{jira_id}")
def api_get_run_jira_data(jira_id: int):
    row = get_run_jira_data_by_id(jira_id)
    if not row:
        raise HTTPException(status_code=404, detail="Jira data not found")
    return row


# ── Run Figma Data ───────────────────────


@router.get("/run-figma-data/{figma_id}")
def api_get_run_figma_data(figma_id: int):
    row = get_run_figma_data_by_id(figma_id)
    if not row:
        raise HTTPException(status_code=404, detail="Figma data not found")
    return row


# ── Run Browser Data ─────────────────────


@router.get("/run-browser-data/{browser_id}")
def api_get_run_browser_data(browser_id: int):
    row = get_run_browser_data_by_id(browser_id)
    if not row:
        raise HTTPException(status_code=404, detail="Browser data not found")
    return row


# ── Run Token Usage ──────────────────────


@router.get("/run-token-usage/{usage_id}")
def api_get_run_token_usage(usage_id: int):
    row = get_run_token_usage_by_id(usage_id)
    if not row:
        raise HTTPException(status_code=404, detail="Token usage record not found")
    return row


# ── Run Plan ────────────────────────────


@router.get("/run-plan")
def api_list_run_plan(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return list_run_plan(limit, offset)


@router.get("/run-plan/{plan_id}")
def api_get_run_plan(plan_id: int):
    row = get_run_plan_by_id(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan step not found")
    return row
