"""CastRender — stores state for the two-pass render pipeline.

Pass 1 bakes each bonded block via InfiniteTalk.
Pass 2 composes the full timeline via FFmpeg.
"""
import enum
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, JSON, Enum, ForeignKey, func, Float
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class CastRenderStatus(str, enum.Enum):
    QUEUED = "queued"
    BAKING = "baking"
    COMPOSING = "composing"
    READY = "ready"
    FAILED = "failed"


class CastRender(Base):
    __tablename__ = "cast_renders"

    id = Column(String(40), primary_key=True)
    cast_id = Column(String(40), ForeignKey("casts.id"), nullable=False, index=True)
    user_id = Column(String(40), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(32), default=CastRenderStatus.QUEUED.value, nullable=False, index=True)
    version = Column(Integer, nullable=True)
    timeline_snapshot = Column(JSON, nullable=False)
    output_video_r2_key = Column(String(512), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    quality = Column(String(20), nullable=True)
    is_selected = Column(Boolean, default=False, nullable=False, server_default="false")
    thumbnail_key = Column(String(512), nullable=True)
    baking_chunks_total = Column(Integer, nullable=True)
    baking_chunks_completed = Column(Integer, default=0)
    render_attempt = Column(Integer, default=0)
    progress_percent = Column(Integer, nullable=True)
    progress_step = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    # Per-block progress. Shape: list of dicts, one per bonded block, in order:
    #   {
    #     "block_id": "blk_...",
    #     "index": 0,
    #     "state": "queued" | "baking" | "done" | "failed",
    #     "provider": "host" | "mod" | "pod" | null,
    #     "started_at": iso8601 | null,
    #     "completed_at": iso8601 | null,
    #     "duration_s": float | null,  # filled when done, used for future ETA
    #     "error": str | null,
    #   }
    # Written incrementally by cast_render.py task during dispatch + completion.
    block_statuses = Column(JSONB, default=list, server_default="[]")
