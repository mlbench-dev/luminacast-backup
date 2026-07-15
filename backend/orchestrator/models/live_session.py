from datetime import datetime
from database import Base
from sqlalchemy import Column, String, Text, Float, Integer, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum


class LiveSessionStatus(str, enum.Enum):
    DRAFT = "draft"
    STARTING = "starting"
    LIVE = "live"
    PAUSED = "paused"
    ENDED = "ended"
    FAILED = "failed"
    SETUP = "setup"      # Go-Live wizard pre-launch state
    COMPOSING = "composing"  # compositor warming up the RTMP feed


class LiveSession(Base):
    __tablename__ = "live_sessions"

    id = Column(String(40), primary_key=True)
    user_id = Column(String(40), ForeignKey("users.id"), nullable=False, index=True)
    avatar_id = Column(String(40), ForeignKey("avatars.id"), nullable=False, index=True)
    title = Column(String(200), nullable=True)
    status = Column(String(20), default="draft", nullable=False, index=True)

    # Product queue — ordered list of product IDs to feature
    product_queue = Column(JSON, default=list)  # [{"product_id": str, "footage_keys": [str], "talking_points": str}]

    # Voice config
    voice_style_notes = Column(Text, nullable=True)

    # Session config
    max_duration_minutes = Column(Integer, default=60)
    output_format = Column(String(10), default="9:16")

    # Runtime state
    current_product_index = Column(Integer, default=0)
    current_paragraph = Column(Text, nullable=True)
    stream_key = Column(String(100), nullable=True)
    hls_url = Column(String(500), nullable=True)

    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    total_paragraphs_generated = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    # ====================================================================
    # Go Live (multi-cast / multi-platform / monitor) extensions.
    # The legacy fields above remain for the voice-only live broadcast MVP;
    # the columns below power the full Go-Live page (cast selection, platform
    # publishing, invited-team monitoring, traction metrics).
    # See docs/Perplexity_GoLive_Architecture.md.
    # ====================================================================

    # Full Go-Live config blob (LiveSessionConfig in the docs):
    #   cast_selections, platforms (RTMP keys), duration_minutes,
    #   background_music_id, chat_reactivity, product_rotation_minutes,
    #   traction_threshold.
    config = Column(JSON, default=dict, nullable=True)

    # Relay stream key the compositor publishes to and the user (or OBS
    # browser source) pulls from: rtmp://relay/.../live/<relay_stream_key>.
    # NOTE: distinct from the legacy `stream_key` field which was used by the
    # voice-only MVP; we keep both for backward compat.
    relay_stream_key = Column(String(64), unique=True, nullable=True)
    # Bearer token for the WS endpoint and the OBS browser-source URL.
    session_token = Column(String(128), nullable=True)

    # Live metrics (best-effort).
    total_viewers = Column(Integer, default=0)
    peak_viewers = Column(Integer, default=0)
    total_purchases = Column(Integer, default=0)
    total_revenue_cents = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)

    # Stream health (last reported by the compositor heartbeat).
    last_bitrate_kbps = Column(Integer, default=0)
    last_dropped_frames = Column(Integer, default=0)

    updated_at = Column(DateTime, onupdate=func.now())

    invites = relationship(
        "LiveSessionInvite",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    events = relationship(
        "LiveSessionEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="LiveSessionEvent.created_at",
    )


class LiveSessionInvite(Base):
    """Invite to monitor or co-admin a live session.

    Roles:
      admin     — full controls (skip / pause / inject / end)
      monitor   — read-only dashboard (metrics + chat + event log)
      moderator — flag chat / mute users (no stream controls)
    """
    __tablename__ = "live_session_invites"

    id = Column(String(40), primary_key=True)
    session_id = Column(
        String(40), ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True
    )
    email = Column(String(255), nullable=False)
    role = Column(String(20), default="monitor", nullable=False)
    access_token = Column(String(128), unique=True, nullable=True)
    accepted = Column(Boolean, default=False, nullable=False)
    invited_at = Column(DateTime, server_default=func.now())
    accepted_at = Column(DateTime, nullable=True)

    session = relationship("LiveSession", back_populates="invites")


class LiveSessionEvent(Base):
    """Audit-trail event for a live session.

    Used by the Monitor dashboard's event log AND by the orchestrator's
    decision history (purchases per minute, traction pivots, etc).
    """
    __tablename__ = "live_session_events"

    id = Column(String(40), primary_key=True)
    session_id = Column(
        String(40), ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True
    )
    event_type = Column(String(40), nullable=False, index=True)
    # stream_started / stream_ended
    # product_started / product_ended / block_played
    # purchase / add_to_cart / chat_reaction / chat_blocked
    # traction_pivot / repeat_cta
    # admin_override / admin_skip / admin_inject_message / admin_pause / admin_end
    # health_warning / health_recovered
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    session = relationship("LiveSession", back_populates="events")
