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
        "tasks.social_tasks",
        "tasks.golive_compositor",
        "tasks.usage_rollup",
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
        "refresh-trending-products": {
            "task": "refresh_trending_products",
            "schedule": crontab(hour=6, minute=0),  # 6 AM UTC daily
        },
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
    },
)
