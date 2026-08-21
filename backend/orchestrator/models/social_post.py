"""Social Post + Social Comment models for Zernio integration.

A SocialPost is a single rendered cast scheduled or published to one or
more platforms via Zernio. SocialComment captures comments fetched back
from Zernio (across platforms) plus the AI-suggested reply.
"""
from __future__ import annotations

from database import Base
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class SocialChannel(Base):
    """A connected social-media account (Zernio-linked).

    A user can have multiple channels per platform (e.g. several TikTok
    handles). Each channel tracks its primary avatar so the Distribute
    UI can warn before posting a cast that uses a *different* avatar to
    a channel followers expect.
    """

    __tablename__ = "social_channels"

    id = Column(String(40), primary_key=True)  # sch_<hex12>
    user_id = Column(
        String(40), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # Platform info
    platform = Column(String(20), nullable=False)  # tiktok / instagram / youtube / linkedin / facebook / x / pinterest
    platform_account_id = Column(String(100), nullable=True)
    handle = Column(String(100), nullable=True)
    display_name = Column(String(200), nullable=True)
    follower_count = Column(Integer, default=0)
    profile_image_url = Column(String(500), nullable=True)

    # Zernio's reference for this account
    zernio_account_id = Column(String(64), nullable=True)

    # Avatar consistency
    primary_avatar_id = Column(
        String(40), ForeignKey("avatars.id", ondelete="SET NULL"), nullable=True
    )
    # avatar_history: [{avatar_id, avatar_name, post_count, first_used, last_used}]
    avatar_history = Column(JSON, nullable=True)

    # Stats
    total_posts = Column(Integer, default=0)
    total_scheduled = Column(Integer, default=0)

    # Status
    status = Column(String(20), nullable=False, default="active")  # active | disconnected | token_expired
    connected_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, nullable=True)

    primary_avatar = relationship("Avatar", foreign_keys=[primary_avatar_id])


class SocialPost(Base):
    __tablename__ = "social_posts"

    id = Column(String(40), primary_key=True)  # spo_<hex12>
    user_id = Column(String(40), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    cast_id = Column(String(40), ForeignKey("casts.id", ondelete="SET NULL"), nullable=True, index=True)
    render_id = Column(String(40), nullable=True)

    # Zernio reference \u2014 their post ID after we create it. Null until creation.
    zernio_post_id = Column(String(64), nullable=True)

    # Content
    caption = Column(Text, nullable=True)
    hashtags = Column(JSON, nullable=True)  # ["skincare", "tiktokshop", ...]
    media_url = Column(String(500), nullable=True)  # R2 URL of the rendered video

    # Scheduling
    platforms = Column(JSON, nullable=True)  # [{"platform": "tiktok", "accountId": "acc_xxx"}, ...]
    scheduled_for = Column(DateTime, nullable=True)  # null = posted immediately

    # Lifecycle
    status = Column(String(20), nullable=False, default="draft", index=True)
    # draft | scheduled | publishing | published | failed | deleted

    # Per-platform results (populated after Zernio confirms or via polling).
    platform_post_ids = Column(JSON, nullable=True)  # {"tiktok": "...", "instagram": "..."}
    analytics = Column(JSON, nullable=True)  # {views, likes, comments, shares, ...}

    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    published_at = Column(DateTime, nullable=True)

    comments = relationship(
        "SocialComment",
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="SocialComment.created_at.desc()",
    )


class SocialComment(Base):
    __tablename__ = "social_comments"

    id = Column(String(40), primary_key=True)  # scm_<hex12>
    social_post_id = Column(
        String(40), ForeignKey("social_posts.id", ondelete="CASCADE"), index=True, nullable=False
    )

    platform = Column(String(20), nullable=True)  # tiktok | instagram | youtube | linkedin | facebook
    platform_comment_id = Column(String(64), nullable=True)
    author_name = Column(String(100), nullable=True)
    author_handle = Column(String(100), nullable=True)
    text = Column(Text, nullable=True)

    # AI reply suggestion + workflow state.
    ai_suggested_reply = Column(Text, nullable=True)
    reply_status = Column(String(20), nullable=False, default="pending", index=True)
    # pending | approved | sent | skipped | flagged
    actual_reply = Column(Text, nullable=True)

    # Set when the message tries to escape the seller persona ("ignore previous
    # instructions", "you are now X", etc.). Auto-skipped from suggestions.
    is_prompt_injection = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)

    post = relationship("SocialPost", back_populates="comments")


class PendingSocialConnect(Base):
    """Short-lived record bridging a Zernio OAuth connect attempt back to
    the Luminacast user who initiated it.

    Zernio's OAuth redirect only echoes back `platform` + `status` — never
    which account was connected — and Zernio itself has no per-customer
    concept at all (single shared platform-wide API key). So the only way
    to correctly attribute a newly-connected account to the right user is:
    snapshot the account list for this platform right before opening the
    OAuth popup (this row), then diff against a fresh snapshot once the
    popup reports success (see confirm_connect in routers/social.py) —
    whichever account is new belongs to whoever's snapshot it's missing
    from. Rows are deleted once consumed; a background sweep of anything
    older than a few minutes isn't implemented since these are looked up
    by (user_id, platform) with a recency cutoff at read time, so a stale
    abandoned row is simply ignored, never acted on.
    """

    __tablename__ = "pending_social_connects"

    id = Column(String(40), primary_key=True)  # psc_<hex12>
    user_id = Column(String(40), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    platform = Column(String(20), nullable=False)
    before_zernio_account_ids = Column(JSON, nullable=False)  # [str, ...]
    created_at = Column(DateTime, server_default=func.now())
