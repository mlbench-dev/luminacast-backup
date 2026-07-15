"""Zernio comment-polling Celery task.

Runs every 5 minutes via Celery beat. For each published post less than
7 days old, pulls new comments from Zernio and generates AI reply
suggestions (skipping prompt-injection attempts).

The task is a no-op when ZERNIO_API_KEY is not configured — the integration
is optional. We still register the schedule so flipping the key on doesn't
require a redeploy.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import sentry_sdk
from sqlalchemy import select

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.social_tasks.poll_comments_task", queue="default")
def poll_comments_task() -> dict:
    """Sync entry point that runs the async poller."""
    try:
        return asyncio.run(_poll_comments_async())
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise


def _make_task_session_factory():
    """Build a session factory bound to THIS task's event loop.

    The shared ``database.async_session_factory`` wraps a module-level engine
    whose pooled asyncpg connections bind to whatever loop first used them. A
    Celery task runs the poller via ``asyncio.run()``, which spins up a FRESH
    loop each run — so reusing the shared pool raises
    "Task got Future attached to a different loop". A task-local engine with
    ``NullPool`` (no cross-run connection reuse) is created and disposed inside
    the same loop, sidestepping the mismatch entirely.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from config import settings

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _poll_comments_async() -> dict:
    from config import settings
    if not settings.ZERNIO_API_KEY:
        return {"skipped": True, "reason": "no_zernio_key"}

    from models.social_post import SocialPost
    from routers.social import _refresh_comments_for_post

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    total_new = 0
    posts_checked = 0

    engine, session_factory = _make_task_session_factory()
    try:
        # Load the candidate post list in a short-lived session, then release it
        # before doing per-post refresh work. Each refresh below opens its own
        # session, so concurrent or long-running refreshes never share an
        # asyncpg connection (which cannot serve concurrent operations).
        try:
            async with session_factory() as db:
                rows = (
                    await db.execute(
                        select(SocialPost).where(
                            SocialPost.status.in_(("published", "scheduled", "publishing")),
                        )
                    )
                ).scalars().all()
                candidates = [
                    {
                        "id": p.id,
                        "published_at": p.published_at,
                        "zernio_post_id": p.zernio_post_id,
                    }
                    for p in rows
                ]
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

        for cand in candidates:
            if cand["published_at"] and cand["published_at"] < cutoff:
                continue
            if not cand["zernio_post_id"]:
                continue
            try:
                async with session_factory() as db:
                    post = await db.get(SocialPost, cand["id"])
                    if post is None:
                        continue
                    added = await _refresh_comments_for_post(db, post)
                    total_new += added
                    posts_checked += 1
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning("poll_comments failed for post %s: %s", cand["id"], e)
    finally:
        # Dispose within the same loop so no asyncpg connection outlives the run.
        try:
            await engine.dispose()
        except Exception as e:
            sentry_sdk.capture_exception(e)

    logger.info(
        "Zernio poll: %d new comments across %d posts",
        total_new, posts_checked,
    )
    return {"posts_checked": posts_checked, "new_comments": total_new}
