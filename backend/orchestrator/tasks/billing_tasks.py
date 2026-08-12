"""Daily billing maintenance — monthly allowance release and credit expiry.

Mirrors `tasks/usage_rollup.py`'s shape (sync Celery entry point delegating
to an async implementation via `async_session_factory`). Runs nightly via
Celery beat (see `tasks/__init__.py`).

- `refresh_usage_periods_task`: ensures every active/past-due subscription
  has a `UsagePeriod` covering today, releasing the next month's allowance
  when the previous one has elapsed (no rollover — see
  `services/billing_service.refresh_usage_periods`), and flips
  subscriptions whose Stripe period lapsed with no renewal to `expired`.
- `expire_credits_task`: zeroes out PAYG credit purchase batches past
  their 12-month expiry.

Both are idempotent — safe to run more than once for the same day (a
period that already exists is left alone; a batch already expired has
`remaining_cents == 0` and is skipped).
"""
from __future__ import annotations

import asyncio
import logging

import sentry_sdk

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.billing_tasks.refresh_usage_periods", queue="default")
def refresh_usage_periods() -> dict:
    try:
        return asyncio.run(_refresh_usage_periods_async())
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise


async def _refresh_usage_periods_async() -> dict:
    from database import async_session_factory
    from services import billing_service

    async with async_session_factory() as db:
        result = await billing_service.refresh_usage_periods(db)
    logger.info("refresh_usage_periods: %s", result)
    return result


@celery_app.task(name="tasks.billing_tasks.expire_credits", queue="default")
def expire_credits() -> dict:
    try:
        return asyncio.run(_expire_credits_async())
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise


async def _expire_credits_async() -> dict:
    from database import async_session_factory
    from services import billing_service

    async with async_session_factory() as db:
        result = await billing_service.expire_credits(db)
    logger.info("expire_credits: %s", result)
    return result
