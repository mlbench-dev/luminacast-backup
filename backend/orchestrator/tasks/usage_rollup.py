"""Daily usage rollup.

Aggregates yesterday's `UsageEvent` rows into one `UsageDailySummary` per
user. Runs nightly at 00:05 UTC via Celery beat (see `tasks/__init__.py`).

Pre-aggregating keeps the user billing page and admin dashboard fast as
the events table grows. The summary is the source of truth for any
historical reporting older than the day-zero retention window.

Idempotency: a (user_id, date) summary is detected and overwritten on
re-run, so a deploy bounce that fires the task twice does not double-count.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone

import sentry_sdk
from sqlalchemy import select

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.usage_rollup.rollup_daily_usage", queue="default")
def rollup_daily_usage() -> dict:
    """Sync entry point — Celery beat invokes this. Delegates to the async
    implementation so we can reuse the project's `async_session_factory`.
    """
    try:
        return asyncio.run(_rollup_daily_usage_async())
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise


async def _rollup_daily_usage_async() -> dict:
    from database import async_session_factory
    from models.usage import UsageDailySummary, UsageEvent

    yesterday = date.today() - timedelta(days=1)
    # SQLAlchemy DateTime columns in this codebase are naive UTC, so build
    # the bounds as naive datetimes for an indexed range scan rather than
    # using `func.date()` which won't hit the index.
    start = datetime.combine(yesterday, time.min)
    end = datetime.combine(yesterday + timedelta(days=1), time.min)

    summaries_written = 0
    summaries_overwritten = 0

    async with async_session_factory() as db:
        user_rows = (
            await db.execute(
                select(UsageEvent.user_id)
                .where(
                    UsageEvent.created_at >= start,
                    UsageEvent.created_at < end,
                )
                .distinct()
            )
        ).all()

        for (user_id,) in user_rows:
            if not user_id:
                continue

            events = (
                await db.execute(
                    select(UsageEvent).where(
                        UsageEvent.user_id == user_id,
                        UsageEvent.created_at >= start,
                        UsageEvent.created_at < end,
                    )
                )
            ).scalars().all()

            # Matches routers/usage.py's render_types — action_render is a
            # real fourth block-render category (see pri01 migration note),
            # not just avatar/motion/pip.
            render_types = ("avatar_render", "motion_render", "pip_render", "action_render")

            totals = {
                "total_provider_cost": sum(e.provider_cost_usd or 0 for e in events),
                "total_user_price": sum(e.user_price_usd or 0 for e in events),
                "render_count": sum(1 for e in events if e.event_type in render_types),
                "render_cost": sum(
                    e.provider_cost_usd or 0
                    for e in events
                    if e.event_type in render_types
                ),
                "render_gpu_seconds": sum(
                    e.quantity or 0
                    for e in events
                    if e.event_type in render_types and e.quantity_unit == "gpu_seconds"
                ),
                "script_gen_count": sum(
                    1 for e in events if e.event_type == "script_generation"
                ),
                "script_gen_cost": sum(
                    e.provider_cost_usd or 0
                    for e in events
                    if e.event_type == "script_generation"
                ),
                "script_gen_tokens": int(
                    sum(
                        e.quantity or 0
                        for e in events
                        if e.event_type == "script_generation"
                        and e.quantity_unit == "tokens"
                    )
                ),
                "tts_count": sum(
                    1 for e in events if e.event_type == "tts_generation"
                ),
                "tts_cost": sum(
                    e.provider_cost_usd or 0
                    for e in events
                    if e.event_type == "tts_generation"
                ),
                "music_count": sum(
                    1 for e in events if e.event_type == "music_generation"
                ),
                "music_cost": sum(
                    e.provider_cost_usd or 0
                    for e in events
                    if e.event_type == "music_generation"
                ),
                "body_shot_count": sum(
                    1 for e in events if e.event_type == "body_shot_generation"
                ),
                "body_shot_cost": sum(
                    e.provider_cost_usd or 0
                    for e in events
                    if e.event_type == "body_shot_generation"
                ),
                "publish_count": sum(
                    1 for e in events if e.event_type == "social_publish"
                ),
                "publish_cost": sum(
                    e.provider_cost_usd or 0
                    for e in events
                    if e.event_type == "social_publish"
                ),
            }

            # Idempotency — if a summary for this (user, date) already
            # exists, overwrite it rather than insert a duplicate. A deploy
            # bounce or a manual re-run shouldn't double-count.
            existing = (
                await db.execute(
                    select(UsageDailySummary).where(
                        UsageDailySummary.user_id == user_id,
                        UsageDailySummary.date == yesterday,
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                summary = UsageDailySummary(
                    id=f"uds_{uuid.uuid4().hex[:12]}",
                    user_id=user_id,
                    date=yesterday,
                    **totals,
                )
                db.add(summary)
                summaries_written += 1
            else:
                for key, value in totals.items():
                    setattr(existing, key, value)
                summaries_overwritten += 1

        await db.commit()

    logger.info(
        "usage_rollup: date=%s users=%d new=%d overwritten=%d",
        yesterday.isoformat(),
        len(user_rows),
        summaries_written,
        summaries_overwritten,
    )

    return {
        "date": yesterday.isoformat(),
        "users_processed": len(user_rows),
        "summaries_written": summaries_written,
        "summaries_overwritten": summaries_overwritten,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
