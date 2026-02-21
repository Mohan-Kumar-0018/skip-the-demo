from __future__ import annotations

from fastapi import APIRouter

from db.models import get_dashboard_stats

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/")
def dashboard_home():
    """Legacy aggregate stats (videos, PDFs, Jiras, avg score)."""
    return {"aggregate_stats": dict(get_dashboard_stats())}
