"""Lightweight API usage logger — writes to api_usage_logs table.

Every call is wrapped in try/except so a logging failure never fails the
(already-succeeded) render/generation it's recording the cost of — losing a
finished video because its cost row couldn't be written would be a worse
outcome than the missing row. But a failure here must never be *invisible*:
it's reported to Sentry so someone actually sees and fixes it, instead of
the row just silently never existing (which is what was happening before —
``user_id=""`` at ~9 call sites was hitting api_usage_logs' FK constraint on
every single call, forever, with only a WARNING buried in log volume as the
only trace).
"""
import logging
import uuid
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)


async def log_api_usage(
    *,
    user_id: Optional[str],
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
    """Insert a row into api_usage_logs. Never raises.

    ``user_id`` must be a real user id or ``None`` — never ``""``. The
    column is nullable (NULL is exempt from the FK check) for genuinely
    user-less events like a scheduled trending-products scrape; an empty
    string is not a valid FK value and always fails the insert.
    """
    try:
        from models.api_usage_log import ApiUsageLog
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from config import settings

        engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        row = ApiUsageLog(
            id=f"usage_{uuid.uuid4().hex[:12]}",
            user_id=user_id or None,
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
        logger.error(f"Usage logging failed: {e}", exc_info=True)
        sentry_sdk.set_context("usage_log", {
            "service": service, "operation": operation, "user_id": user_id or "",
        })
        sentry_sdk.capture_exception(e)
