"""Celery application configuration."""
from celery import Celery
from config import settings
from logging_config import setup_logging
from services.sentry import init_sentry

# Initialize logging and Sentry for Celery workers
setup_logging()
init_sentry()

celery_app = Celery(
    "luminacast",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "tasks.generate_avatar",
        "tasks.generate_cast",
        "tasks.stream_worker",
        "tasks.index_channel",
        "tasks.product_tasks",
        "tasks.auto_music",
        "tasks.music",
        "tasks.avatar_looks",
        "tasks.voice_corpus",
        "tasks.live_reference",
        "tasks.live_session",
        "tasks.clone_tiktok_scout",
        "tasks.pipeline_steps",
        "tasks.acting_video",
        "tasks.cast_render",
        "tasks.smart_cast_tasks",
        "tasks.product_broll_tasks",
        "tasks.social_tasks",
        "tasks.golive_compositor",
        "tasks.usage_rollup",
        "tasks.billing_tasks",
    ],
)

from celery.schedules import crontab

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "tasks.cast_render.render": {"queue": "renders"},
    },
    beat_schedule={
        "refresh-platform-intelligence": {
            "task": "tasks.generate_cast.refresh_intelligence",
            "schedule": 3600.0,  # every hour
        },
        "cleanup-stale-rendering": {
            "task": "cleanup_stale_rendering_jobs",
            "schedule": crontab(minute="*/15"),  # Every 15 minutes
        },
        # Separate from the job above — that one only cleans up the legacy
        # Variant/RunPod-webhook pipeline. This one reaps CastRender rows
        # (the pipeline actually in use today) that get hard-killed by the
        # task's own 3600s Celery time_limit, which never runs cleanup code,
        # leaving status="baking" forever otherwise. 5-minute cadence keeps
        # detection lag small relative to the 18-minute staleness window.
        "cleanup-stale-cast-renders": {
            "task": "cleanup_stale_cast_renders",
            "schedule": crontab(minute="*/5"),
        },
        # Safety net for avatar-creation WaveSpeed webhook deliveries that
        # never arrive (e.g. the orchestrator was mid-deploy when WaveSpeed
        # called back) — same idea as cleanup-stale-rendering above, but for
        # the avatar test-video preview path. See
        # tasks.generate_avatar.reconcile_stale_avatar_wavespeed_jobs.
        "reconcile-stale-avatar-wavespeed-jobs": {
            "task": "reconcile_stale_avatar_wavespeed_jobs",
            "schedule": crontab(minute="*/5"),
        },
        # DISABLED: this ran a full `maxItems: 200` parseforge trending scrape
        # per section x 5 sections, plus up to 15 pro100chok enrichment scrapes
        # per section, every day — with force_refresh=True bypassing the 24h
        # cache entirely (services/product_discovery.fetch_trending). It re-
        # scraped roughly the same products daily and was the main driver of
        # the client's ~$250/mo Apify bill. Nothing consumes trending_products
        # anymore: the Discover tab is disabled (routers/product_discovery.py
        # _DISCOVER_DISABLED) and product import is URL-only. Re-enable only if
        # the Discover tab is restored AND the cache is respected.
        # "refresh-trending-products": {
        #     "task": "refresh_trending_products",
        #     "schedule": crontab(hour=6, minute=0),  # 6 AM UTC daily
        # },
        # Zernio: pull new comments and generate AI replies for recent posts.
        # Every 5 minutes is the cadence the Zernio integration doc requested.
        "poll-social-comments": {
            "task": "tasks.social_tasks.poll_comments_task",
            "schedule": 300.0,
        },
        # Daily UsageEvent → UsageDailySummary rollup. 00:05 UTC keeps it
        # outside the midnight cron crowd while still landing in the new day.
        "rollup-daily-usage": {
            "task": "tasks.usage_rollup.rollup_daily_usage",
            "schedule": crontab(hour=0, minute=5),
        },
        # Releases next month's render/live-stream allowance for
        # subscriptions whose current UsagePeriod has elapsed, and expires
        # subscriptions whose Stripe period lapsed with no renewal.
        "refresh-billing-usage-periods": {
            "task": "tasks.billing_tasks.refresh_usage_periods",
            "schedule": crontab(hour=0, minute=10),
        },
        # Forfeits PAYG credit purchase batches past their 12-month expiry.
        "expire-payg-credits": {
            "task": "tasks.billing_tasks.expire_credits",
            "schedule": crontab(hour=0, minute=15),
        },
    },
)
