"""Rolling ETA estimator from historical render_jobs data.

Provides median completion time, queue position, and confidence-tagged
wait estimates for the RenderStatusBanner.
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_eta(db: AsyncSession, job_type: str) -> int | None:
    """Median (completed_at - queued_at) in seconds from the last 20 COMPLETED jobs of this type.

    Returns None if fewer than 3 samples exist.
    """
    row = await db.execute(
        text("""
            SELECT percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (completed_at - queued_at))
            ) AS median_seconds,
            COUNT(*) AS sample_count
            FROM (
                SELECT completed_at, queued_at
                FROM render_jobs
                WHERE job_type = :job_type
                  AND state = 'COMPLETED'
                  AND completed_at IS NOT NULL
                  AND queued_at IS NOT NULL
                ORDER BY completed_at DESC
                LIMIT 20
            ) recent
        """),
        {"job_type": job_type},
    )
    result = row.fetchone()
    if not result or result.sample_count < 3:
        return None
    return int(result.median_seconds) if result.median_seconds else None


async def get_queue_position(db: AsyncSession, job_type: str, provider: str) -> int:
    """Count of QUEUED + IN_PROGRESS jobs ahead of the caller."""
    row = await db.execute(
        text("""
            SELECT COUNT(*) AS position
            FROM render_jobs
            WHERE job_type = :job_type
              AND provider = :provider
              AND state IN ('QUEUED', 'IN_PROGRESS')
        """),
        {"job_type": job_type, "provider": provider},
    )
    result = row.fetchone()
    return result.position if result else 0


async def estimate_wait_seconds(
    db: AsyncSession, job_type: str, provider: str
) -> dict:
    """Full ETA estimate with confidence level.

    Returns:
        {
            position: int,
            median_per_job: int | None,
            estimated_seconds: int | None,
            confidence: 'high' | 'low' | 'none'
        }
    """
    position = await get_queue_position(db, job_type, provider)
    median = await get_eta(db, job_type)

    # Determine confidence from sample count
    row = await db.execute(
        text("""
            SELECT COUNT(*) AS cnt
            FROM render_jobs
            WHERE job_type = :job_type
              AND state = 'COMPLETED'
              AND completed_at IS NOT NULL
              AND queued_at IS NOT NULL
        """),
        {"job_type": job_type},
    )
    count_result = row.fetchone()
    sample_count = count_result.cnt if count_result else 0

    if sample_count >= 10:
        confidence = "high"
    elif sample_count >= 3:
        confidence = "low"
    else:
        confidence = "none"

    estimated_seconds = None
    if median is not None and position > 0:
        estimated_seconds = median * position
    elif median is not None:
        estimated_seconds = median

    return {
        "position": position,
        "median_per_job": median,
        "estimated_seconds": estimated_seconds,
        "confidence": confidence,
    }
