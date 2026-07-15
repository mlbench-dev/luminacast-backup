from database import Base
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.sql import func


class UsageEvent(Base):
    """One row per billable provider action.

    Written by `services.usage_tracker.log_usage()`. Caller sets `id` to
    `f"usg_{uuid4().hex[:12]}"` matching the convention used by every other
    model in this package.
    """

    __tablename__ = "usage_events"

    id = Column(String, primary_key=True)  # prefix: usg_
    user_id = Column(String, ForeignKey("users.id"), index=True)

    # What happened. Values: script_generation, tts_generation, avatar_render,
    # motion_render, pip_render, body_shot_generation, music_generation,
    # stock_search, social_publish, comment_reply, live_stream_minute,
    # voice_clone_analysis, product_import, ffmpeg_compose, whisperx_transcription
    event_type = Column(String(50), index=True)

    # Which resource the event was charged against
    resource_type = Column(String(30), nullable=True)  # cast | avatar | product | live_session
    resource_id = Column(String, nullable=True)
    render_id = Column(String, nullable=True)
    block_id = Column(String, nullable=True)

    # Provider name. Values: hostkey, runpod, modal, fal_ai, openrouter,
    # mubert, zernio, apify, pexels
    provider = Column(String(30))

    # Cost in USD. provider_cost_usd = what we paid the provider.
    # user_price_usd = provider_cost_usd * MARKUP_MULTIPLIER (set by caller).
    provider_cost_usd = Column(Float, default=0.0)
    user_price_usd = Column(Float, default=0.0)

    # Metric driving the cost (tokens, gpu_seconds, video_seconds, images,
    # audio_seconds, bytes, calls, posts, tracks, megapixels)
    quantity = Column(Float, default=1.0)
    quantity_unit = Column(String(20), default="call")

    # Provider-side context for traceability
    provider_job_id = Column(String(100), nullable=True)
    provider_model = Column(String(100), nullable=True)  # e.g. "anthropic/claude-sonnet-4.6"
    duration_seconds = Column(Float, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), index=True)


class UsageDailySummary(Base):
    """One row per (user, date) — populated by the daily rollup task (PR γ).

    Pre-aggregating UsageEvent into this table keeps the user billing page
    and admin dashboard fast as the events table grows.
    """

    __tablename__ = "usage_daily_summaries"

    id = Column(String, primary_key=True)  # prefix: uds_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    date = Column(Date, index=True)

    total_provider_cost = Column(Float, default=0.0)
    total_user_price = Column(Float, default=0.0)

    render_count = Column(Integer, default=0)
    render_cost = Column(Float, default=0.0)
    render_gpu_seconds = Column(Float, default=0.0)

    script_gen_count = Column(Integer, default=0)
    script_gen_cost = Column(Float, default=0.0)
    script_gen_tokens = Column(Integer, default=0)

    tts_count = Column(Integer, default=0)
    tts_cost = Column(Float, default=0.0)

    music_count = Column(Integer, default=0)
    music_cost = Column(Float, default=0.0)

    body_shot_count = Column(Integer, default=0)
    body_shot_cost = Column(Float, default=0.0)

    publish_count = Column(Integer, default=0)
    publish_cost = Column(Float, default=0.0)

    storage_bytes_added = Column(BigInteger, default=0)

    created_at = Column(DateTime, server_default=func.now())
