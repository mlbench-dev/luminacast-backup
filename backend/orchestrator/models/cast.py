import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class CastStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OUTLINE_REVIEW = "OUTLINE_REVIEW"
    SCRIPT_REVIEW = "SCRIPT_REVIEW"
    TEMPLATE_SELECT = "TEMPLATE_SELECT"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    GENERATING_TTS = "generating_tts"
    TTS_READY = "tts_ready"
    GENERATING_VIDEOS = "generating_videos"
    GENERATING = "GENERATING"
    GENERATION_FAILED = "GENERATION_FAILED"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    COMPLETED = "COMPLETED"


class CastApprovalStatus(str, enum.Enum):
    """Teams review workflow — deliberately separate from CastStatus.

    CastStatus drives the render pipeline's own state machine (draft →
    generating → ready → scheduled → live → completed) and is referenced
    in 60+ places across routers/tasks; repurposing it for content review
    would risk colliding with pipeline transitions that have nothing to
    do with who's allowed to publish. This tracks only: has a Creator
    submitted this for review, and has a Publisher approved it. Nothing
    reaches Zernio unless this is APPROVED (enforced in
    routers/social.py::create_social_post), independent of whatever
    CastStatus the render pipeline is currently in.
    """
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"


class CastQuality(str, enum.Enum):
    SIMPLE = "simple"
    HD = "hd"
    HD_PLUS = "hd_plus"


class Cast(Base):
    __tablename__ = "casts"
    id = Column(String, primary_key=True)  # prefix: cst_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    avatar_id = Column(String, ForeignKey("avatars.id"), index=True)
    channel_id = Column(String, ForeignKey("channels.id"), nullable=True, index=True)
    name = Column(String, nullable=True)
    status = Column(Enum(CastStatus, values_callable=lambda x: [e.value for e in x]), default=CastStatus.DRAFT, index=True)
    # Teams review workflow — see CastApprovalStatus docstring. Separate
    # from `status` above (the render pipeline's own state machine).
    # native_enum=False: plain VARCHAR, matching what the migration actually
    # created (unlike `status` above, there's no Postgres CREATE TYPE for
    # this one — a native enum column here would require the DB to have a
    # matching enum type, which doesn't exist).
    approval_status = Column(
        Enum(CastApprovalStatus, values_callable=lambda x: [e.value for e in x], native_enum=False, length=20),
        default=CastApprovalStatus.DRAFT, index=True, nullable=False,
    )
    submitted_for_review_at = Column(DateTime, nullable=True)
    submitted_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    template_name = Column(String, nullable=True)
    # Stage-1 creative template the user picked at SetupPhase (see
    # services.cast_templates). Null = "Auto / let AI choose" — the outline
    # generator decides the structure freely. When set, the outline generator
    # is constrained to the template's block sequence + bias ratios.
    # nullable so casts created before the column existed read fine.
    template_id = Column(String(64), nullable=True)
    schedule_at = Column(DateTime, nullable=True)
    schedule_timezone = Column(String, nullable=True)
    max_duration_minutes = Column(Integer, default=240)
    loop = Column(Boolean, default=True)
    quality = Column(Enum(CastQuality, values_callable=lambda x: [e.value for e in x]), default=CastQuality.SIMPLE)
    output_format = Column(String(10), default="9:16", nullable=False, server_default="9:16")
    r2_manifest_key = Column(String, nullable=True)

    layout_config = Column(JSON, default=None)
    effects_config = Column(JSON, default=None)

    # Twick timeline data (per-variant)
    timeline_json = Column(JSON, nullable=True)

    # AI-generated background music (Mubert v3). Auto-populated when the
    # cast is first created; the editor places this on the lowest audio
    # track at 15% volume, ducked under the voice during render. The user
    # can mute, replace, or remove it.
    background_music_url = Column(Text, nullable=True)
    background_music_mood = Column(String(40), nullable=True)
    background_music_tags = Column(JSON, nullable=True)  # list[str]
    # How the user wants background music handled. "off" skips music entirely
    # (no Mubert generation, no timeline element); "auto" keeps the default
    # mood-driven Mubert generation; "track_id:<id>" pins a fixed library
    # track (see services.music_library). Default "auto" preserves the
    # existing behavior for casts created before the column existed.
    # server_default lets reads work before the manual migration runs.
    music_track_choice = Column(
        String(64), nullable=False, default="auto", server_default="auto"
    )
    # Per-cast music volume override (0.0–0.4). Null falls back to the
    # MUSIC_DEFAULT_VOLUME env (0.15). Set from the visual-studio music panel.
    music_volume = Column(Float, nullable=True)

    # Default visual source for stock_photo/stock_video blocks that have a
    # product attached and no per-block override (Block.video_asset_id /
    # image_asset_id — see ScriptPhase's per-block picker). "stock" (default)
    # auto-populates from Pexels as before; "ai_generated" instead generates
    # a product-only photo/video (FLUX Kontext / Kling, see
    # services/product_ai_media.py) from the product's own reference photo.
    # Set from the Setup-tab picker at cast creation.
    broll_media_source = Column(
        String(20), nullable=False, default="stock", server_default="stock"
    )

    # Cast-wide default avatar background look. Picked at SetupPhase
    # creation; new blocks created via Smart Cast outline inherit this
    # via Block.avatar_look_id. The user can override per-block in
    # ScriptPhase. Null = avatar's own default look.
    default_avatar_look_id = Column(String(40), ForeignKey("avatar_looks.id"), nullable=True, index=True)

    # Step 3 — which layout-template preset this cast resolved to at generation
    # time (Step 2 selector picks it from the detected content type). Read-only
    # for now: the composer/timeline_builder/renderer do NOT consume it yet
    # (Step 4). ON DELETE SET NULL so deleting a preset never orphans a cast.
    layout_template_id = Column(
        String, ForeignKey("layout_templates.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # Active caption preset — drives BOTH the Remotion preview component
    # and the FFmpeg drawtext fallback so preview ≈ render. Stored as a
    # JSON object: {name, fontFamily, fontWeight, fontSize, color,
    # strokeColor, strokeWidth, position, animation, activeWordColor, ...}.
    # See frontend/companion-app/src/lib/caption-presets.ts for the canonical shape.
    caption_preset = Column(JSON, nullable=True)

    # Final composited video (set on render completion)
    final_video_url = Column(String, nullable=True)

    # Progress
    progress_percent = Column(Integer, default=0)
    progress_step = Column(String, default="")
    total_clips = Column(Integer, default=0)
    completed_clips = Column(Integer, default=0)

    # Pricing
    base_price = Column(Float, default=14.99)
    effects_price = Column(Float, default=0)
    total_price = Column(Float, default=14.99)
    creation_fee_cents = Column(Integer, nullable=True)
    creation_paid = Column(Boolean, default=False)
    stripe_payment_id = Column(String, default="")
    generation_progress = Column(Float, default=0.0)
    generation_error = Column(String, nullable=True)
    script_direction = Column(Text, nullable=True)
    target_audience_override = Column(JSON, nullable=True)
    cast_type = Column(String(20), default="recorded", nullable=False, server_default="recorded")

    # PR #162 — Stage-1 LIVE/Recorded toggle payload.
    # `live_mode_defaults`: the LIVE-mode preset object the SetupPhase posts
    # (voiceover on/off, b-roll on/off, max duration, b-roll cadence, etc.).
    # Surfaced to the outline generator as a prompt-context section so the LLM
    # honors the creator's defaults. Null = Recorded cast / pre-migration row.
    # `user_video_ids`: list of UserVideoAsset.id strings the user picked as
    # preferred b-roll sources; their R2 URLs feed the b-roll/overlay pipeline.
    live_mode_defaults = Column(JSON, nullable=True)
    user_video_ids = Column(JSON, nullable=True)  # list[str] of UserVideoAsset.id

    # Phase B: intent-driven script generation
    description = Column(Text, nullable=True)  # User's goal for this cast
    duration_target_seconds = Column(Integer, nullable=True)  # Manual duration override
    platform_target = Column(String(20), nullable=True, server_default="tiktok")  # tiktok/instagram_reels/youtube_shorts

    # Production level: quick | standard | premium. Drives the outline
    # generator's block-category mix — quick = mostly avatar_speaking,
    # standard = mixed shots + b-roll, premium = full mix incl. motion +
    # effects. Picked at SetupPhase; replaces the legacy AI-plan chip preview.
    production_level = Column(String(20), nullable=False, server_default="standard", default="standard")

    # Whether the user had the "Auto Cast" toggle on at creation time. Set
    # once, at creation, from the same choice that decides whether the
    # initial outline is built via generateSmartOutline (AI picks avatar/PIP
    # split) or generateOutline (plain manual blocks) — never read by any
    # backend logic afterward, only stored so SetupPhase can restore the
    # toggle to what the user actually left it as. Nullable: existing casts
    # created before this column existed have no recorded value.
    auto_cast = Column(Boolean, nullable=True, default=None)

    # Phase 2.4 — Cross-format cast support (Pattern C)
    format_family = Column(String(20), default="vertical", nullable=False, server_default="vertical", index=True)
    # values: "vertical" (1080x1920) or "horizontal" (1920x1080)
    parent_cast_id = Column(String, ForeignKey("casts.id", ondelete="SET NULL"), nullable=True, index=True)
    # set when this cast was duplicated from another (for cross-format linkage)
    target_platforms = Column(JSON, default=list)
    # array of strings: ["tiktok", "reels", "shorts"] for vertical; ["youtube", "twitter", "linkedin"] for horizontal

    # Cast versioning — auto-snapshot on fork
    version = Column(Integer, default=1, nullable=False, server_default="1")

    # Phase 2.5 — Linked cascading update across format siblings
    # Naive UTC column (TIMESTAMP WITHOUT TIME ZONE). Writers MUST store a
    # naive value — see _mark_variant_audio_stale in routers/casts/variants.py,
    # which strips tzinfo. Passing an offset-aware datetime here makes asyncpg
    # raise "can't subtract offset-naive and offset-aware datetimes" at commit.
    audio_stale_since = Column(DateTime, nullable=True)
    # set when a sibling cascade updates audio; cleared when user refreshes

    # Auto-Clips (CHANGE 5/6) — `suggested_clips` is the LLM's output from
    # cast_generator.suggest_clips; `approved_clips` is the subset the user
    # kept in the Script Editor. Each child cast (clip_parent_cast_id set)
    # stitches a subset of the parent's blocks together via FFmpeg trim of
    # the parent's already-composed mp4 — no GPU re-render. We use a
    # dedicated `clip_parent_cast_id` rather than reusing the cross-format
    # `parent_cast_id` because the lifecycles differ (clips cascade-delete,
    # cross-format siblings stay around when a sibling is removed).
    suggested_clips = Column(JSON, nullable=True)
    approved_clips = Column(JSON, nullable=True)
    clip_parent_cast_id = Column(String, ForeignKey("casts.id", ondelete="CASCADE"), nullable=True, index=True)
    clip_block_ids = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    # Explicit foreign_keys: submitted_by/approved_by are also FKs to
    # users.id (Teams review workflow) — without this, SQLAlchemy can't
    # tell which of the three columns backs this relationship.
    user = relationship("User", back_populates="casts", foreign_keys=[user_id])
    avatar = relationship("Avatar")
    channel = relationship("Channel")
    blocks = relationship("Block", back_populates="cast", order_by="Block.position")
    products = relationship("CastProduct", back_populates="cast")
    stream_sessions = relationship("StreamSession", back_populates="cast")


class CastVersion(Base):
    """Lightweight snapshot of a cast's blocks at a point in time (fork versioning)."""
    __tablename__ = "cast_versions"
    id = Column(String, primary_key=True)
    cast_id = Column(String, ForeignKey("casts.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    blocks_snapshot_json = Column(JSON, nullable=False)
    status_at_snapshot = Column(String(30), nullable=True)
    change_summary = Column(String(200), nullable=True)
    name = Column(String(200), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    quality = Column(String(20), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CastProduct(Base):
    __tablename__ = "cast_products"
    id = Column(String, primary_key=True)  # prefix: cp_
    cast_id = Column(String, ForeignKey("casts.id"), index=True)
    product_id = Column(String, ForeignKey("products.id"), index=True)
    position = Column(Integer, default=0)
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    cast = relationship("Cast", back_populates="products")
    product = relationship("Product")
