"""LiveReference — a past live session the creator uploads so the script
engine can learn their selling cadence/structure (NOT their persona).

A reference is scoped to EXACTLY ONE of avatar_id / cast_id:
  - avatar-scoped: applies as a default to every cast for that avatar
  - cast-scoped:   applies to one specific cast only (and wins over the
                   avatar default — see engine.live_style.resolve_active_assessment)

The pipeline: uploaded -> transcribing -> transcribed -> assessed (or failed).
``assessment`` is the distilled Live Style Assessment (Step 2); the raw
transcript is kept so we can re-assess if the schema changes.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from database import Base


class LiveReference(Base):
    __tablename__ = "live_references"

    id = Column(String(40), primary_key=True)  # prefix: lref_
    avatar_id = Column(String, ForeignKey("avatars.id"), nullable=True, index=True)
    cast_id = Column(String, ForeignKey("casts.id"), nullable=True, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    source_r2_key = Column(String(500), nullable=True)
    media_kind = Column(String(10), nullable=False, default="video")  # 'audio' | 'video'
    duration_seconds = Column(Float, nullable=True)

    transcript_text = Column(Text, nullable=True)
    transcript_segments = Column(JSONB, nullable=True)  # [{text, start, end}, ...]
    assessment = Column(JSONB, nullable=True)  # Step 2 — distilled Live Style Assessment

    # uploaded | transcribing | transcribed | assessed | failed
    status = Column(String(20), nullable=False, default="uploaded", index=True)
    error_message = Column(Text, nullable=True)

    # The one assessment actually used for future scripts in this scope
    # (avatar_id or cast_id — whichever is set). Only ever true on an
    # "assessed" row; at most one per scope, enforced by a partial unique
    # index in the migration. Newly-assessed uploads auto-activate, but the
    # user can switch back to an earlier one instead of re-uploading it.
    is_active = Column(Boolean, nullable=False, default=False, server_default="false")

    # Per-record portion of this user's rolling 30-day transcription spend.
    # Summed across the user's recent records to enforce LIVE_REF_MONTHLY_CAP_CENTS.
    monthly_spend_cents = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), onupdate=func.now(), nullable=False)


class LiveReferenceExemplar(Base):
    """One exemplar snippet distilled from a live reference's assessment.

    Created in Step 2 so Step 5 can rank exemplars by relevance to the
    product/brief. When embedding infra is available the ``embedding`` column
    holds the vector; this deploy has no embedding helper, so we store the
    text only and Step 5 ranks by token overlap (see engine.live_style).
    """
    __tablename__ = "live_reference_exemplars"

    id = Column(String(40), primary_key=True)  # prefix: lrex_
    live_reference_id = Column(String(40), ForeignKey("live_references.id", ondelete="CASCADE"), nullable=False, index=True)
    beat = Column(String(20), nullable=False)  # hook | demo | objection | cta
    text = Column(Text, nullable=False)
    # Embedding stored as JSON array of floats when an embedding helper exists;
    # null on this deploy (token-overlap fallback). Kept as JSONB rather than a
    # pgvector column so the migration needs no extension.
    embedding = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
