from __future__ import annotations

from fastapi import APIRouter

from db.models import (
    get_dashboard_aggregate,
    get_dashboard_cost_breakdown,
    get_dashboard_failures,
    get_dashboard_funnel,
    get_dashboard_overview,
    get_dashboard_stats,
    get_dashboard_step_durations,
    get_dashboard_step_reliability,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/")
def dashboard_home():
    """Legacy aggregate stats (videos, PDFs, Jiras, avg score)."""
    return {"aggregate_stats": dict(get_dashboard_stats())}


@router.get("/overview")
def dashboard_overview():
    """One row per run with key metrics: duration, cost, score, delivery status."""
    return {"runs": get_dashboard_overview()}


@router.get("/stats")
def dashboard_stats():
    """Top-line numbers: total runs, success rate, avg score, total cost."""
    return get_dashboard_aggregate()


@router.get("/cost-breakdown")
def dashboard_cost_breakdown():
    """Cost and token usage broken down by agent."""
    return {"agents": get_dashboard_cost_breakdown()}


@router.get("/step-reliability")
def dashboard_step_reliability():
    """Success/failure/pending counts per step across all runs."""
    return {"steps": get_dashboard_step_reliability()}


@router.get("/step-durations")
def dashboard_step_durations():
    """Per-step execution duration from the plan table."""
    return {"steps": get_dashboard_step_durations()}


@router.get("/failures")
def dashboard_failures():
    """Recent failed steps with error messages and agent names."""
    return {"failures": get_dashboard_failures()}


@router.get("/funnel")
def dashboard_funnel():
    """Pipeline funnel: how far each run progressed before stopping."""
    return {"runs": get_dashboard_funnel()}
