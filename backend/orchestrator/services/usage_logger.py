"""Lightweight API usage logger — writes to api_usage_logs table.

Every call is wrapped in try/except so logging failures never crash the pipeline.
"""
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


async def log_api_usage(
    *,
    user_id: str,
    service: str,
    operation: str,
    success: bool = True,
    avatar_id: Optional[str] = None,
    cast_id: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    cost_cents: int = 0,
    runpod_job_id: Optional[str] = None,
    error_message: Optional[str] = None,
    input_size_bytes: Optional[int] = None,
    output_size_bytes: Optional[int] = None,
) -> None:
    """Insert a row into api_usage_logs. Never raises."""
    try:
        from models.api_usage_log import ApiUsageLog
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from config import settings

        engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        row = ApiUsageLog(
            id=f"usage_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            avatar_id=avatar_id,
            cast_id=cast_id,
            service=service,
            operation=operation,
            duration_seconds=duration_seconds,
            input_size_bytes=input_size_bytes,
            output_size_bytes=output_size_bytes,
            cost_cents=cost_cents,
            runpod_job_id=runpod_job_id,
            success=success,
            error_message=error_message[:500] if error_message else None,
        )
        async with factory() as session:
            session.add(row)
            await session.commit()
    except Exception as e:
        logger.warning(f"Usage logging failed (non-fatal): {e}")
