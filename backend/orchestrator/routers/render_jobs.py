"""Render Jobs — status endpoint for honest render tracking."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sa_text
from database import get_db
from models.user import User
from routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/render-jobs", tags=["render-jobs"])


@router.get("/{render_job_id}/status")
async def get_render_job_status(
    render_job_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return state, progress_percent, ETA block, error_message for a render job."""
    row = await db.execute(
        sa_text("""
            SELECT rj.id, rj.state, rj.progress_percent, rj.error_message,
                   rj.job_type, rj.provider, rj.created_at, rj.queued_at,
                   rj.started_at, rj.completed_at, rj.failed_at,
                   EXTRACT(EPOCH FROM (NOW() - rj.queued_at)) AS elapsed_seconds
            FROM render_jobs rj
            WHERE rj.id = :rj_id
        """),
        {"rj_id": render_job_id},
    )
    result = row.fetchone()
    if not result:
        raise HTTPException(404, "Render job not found")

    from services.render_eta import estimate_wait_seconds
    eta = await estimate_wait_seconds(db, result.job_type, result.provider)

    return {
        "id": result.id,
        "state": result.state,
        "progress_percent": result.progress_percent,
        "error_message": result.error_message,
        "eta": {
            "position": eta["position"],
            "median_per_job": eta["median_per_job"],
            "estimated_seconds": eta["estimated_seconds"],
            "confidence": eta["confidence"],
        },
        "elapsed_seconds": int(result.elapsed_seconds) if result.elapsed_seconds else None,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "completed_at": result.completed_at.isoformat() if result.completed_at else None,
    }
