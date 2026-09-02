import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class BlockType(str, enum.Enum):
    INTRO = "INTRO"
    HOOK = "HOOK"
    PRODUCT = "PRODUCT"
    PRODUCT_DEMO = "PRODUCT_DEMO"
    TESTIMONIAL = "TESTIMONIAL"
    FEATURE_SHOWCASE = "FEATURE_SHOWCASE"
    COMPARISON = "COMPARISON"
    SOCIAL_PROOF = "SOCIAL_PROOF"
    FLASH_SALE = "FLASH_SALE"
    URGENCY = "URGENCY"
    QA = "QA"
    EDUCATIONAL = "EDUCATIONAL"
    STORY = "STORY"
    CTA = "CTA"
    CLOSING = "CLOSING"
    TRANSITION = "TRANSITION"
    FILLER = "FILLER"
    IDLE = "IDLE"

    @classmethod
    def coerce(cls, raw, default: "BlockType" = None) -> "BlockType":
        """Map an LLM-provided block_type string to a BlockType, case-insensitively.

        LLM outlines emit block_type in lowercase ("product", "cta", "hook") and
        sometimes with friendly synonyms. The enum *values* are upper-case, so a
        bare ``BlockType(raw)`` raised ValueError on every block and the caller's
        ``except`` collapsed the whole cast to PRODUCT (cst_0f43a80b624d — outline
        type variety regressed to all-PRODUCT). Normalize here so both cast-create
        paths agree. Falls back to ``default`` (PRODUCT when unset) for
        empty/unknown values.
        """
        # Defined locally — a dict class attribute would be misread as an enum
        # member by EnumMeta.
        synonyms = {
            "call_to_action": "CTA",
            "outro": "CLOSING",
            "ending": "CLOSING",
            "demo": "PRODUCT_DEMO",
            "review": "TESTIMONIAL",
            "feature": "FEATURE_SHOWCASE",
            "transition_beat": "TRANSITION",
        }
        fallback = default if default is not None else cls.PRODUCT
        if isinstance(raw, cls):
            return raw
        if not raw or not isinstance(raw, str):
            return fallback
        key = raw.strip().lower()
        mapped = synonyms.get(key, key).upper()
        try:
            return cls(mapped)
        except ValueError:
            return fallback


class BlockCategory(str, enum.Enum):
    AVATAR_SPEAKING = "avatar_speaking"
    AVATAR_BODY_MOTION = "avatar_body_motion"
    VOICEOVER_VIDEO = "voiceover_video"
    VOICEOVER_STOCK_VIDEO = "voiceover_stock_video"
    VOICEOVER_PHOTO = "voiceover_photo"
    VOICEOVER_STOCK_PHOTO = "voiceover_stock_photo"
    PIP = "pip"
    MUSIC_ONLY = "music_only"
    PRODUCT_SHOWCASE = "product_showcase"


class LayoutMode(str, enum.Enum):
    FULL_AVATAR = "full_avatar"
    AVATAR_FULL = "avatar_full"  # alias — frontend uses this
    AVATAR_PRODUCT = "avatar_product"
    SPLIT_SCREEN = "split_screen"
    PIP_PRODUCT = "pip_product"
    PRODUCT_ONLY = "product_only"
    AUTO_BASKET = "auto_basket"


class PipLayout(str, enum.Enum):
    """Talking-head PIP window layout. Lives inside block_metadata.pip_layout.

    fullscreen : avatar fills the 1080×1920 canvas (legacy default for
                 greeting / closing / direct-camera CTA).
    pip_small  : ~18% of canvas height (346×346), top-left corner, rounded
                 square with feathered edge + drop shadow. Default for
                 product reveals, feature mentions, explanation beats —
                 anywhere AI artifacts are best suppressed.
    pip_medium : ~25% of canvas height (480×480), top-left corner. For
                 blocks where the creator presence is primary but not
                 full-frame.
    hidden     : audio plays, no face is rendered (the V1 element is
                 omitted from the timeline). The user keeps the
                 narration but the picture comes entirely from
                 background / parallel media.
    """
    FULLSCREEN = "fullscreen"
    PIP_SMALL = "pip_small"
    PIP_MEDIUM = "pip_medium"
    HIDDEN = "hidden"


class Block(Base):
    __tablename__ = "blocks"
    id = Column(String, primary_key=True)  # prefix: blk_
    cast_id = Column(String, ForeignKey("casts.id"), index=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=True, index=True)
    type = Column(Enum(BlockType))
    category = Column(String(30), default="avatar_speaking", nullable=False)
    position = Column(Integer)
    layout_mode = Column(Enum(LayoutMode), default=LayoutMode.FULL_AVATAR)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    background_id = Column(String, ForeignKey("avatar_backgrounds.id"), nullable=True, index=True)

    # Per-block mic flag. NULL = unset/inherit; Step 5 stamps it from the cast's
    # layout template (template.config.voice.mic == "on"). The actual audio-chain
    # wiring (Step 7) and the FLUX mic-on look variant (Step 8) consume it later.
    mic_on = Column(Boolean, nullable=True, index=True)

    # Render mode: avatar_full (default), voiceover (no avatar), pip (picture-in-picture), body_motion
    render_mode = Column(String(20), default="avatar_full", nullable=False)
    user_video_asset_id = Column(String(40), ForeignKey("user_video_assets.id"), nullable=True)
    avatar_look_id = Column(String(40), ForeignKey("avatar_looks.id"), nullable=True)

    # Block type: speaking (default), acting, video, image, voiceover
    block_type = Column(String(20), default="speaking", nullable=False)

    # Per-block avatar angle for render snapshot
    avatar_angle = Column(String(32), default="front", nullable=True)

    # Per-block camera framing (Round-6 Bug B). One of ShotFraming; default
    # MEDIUM. Drives which avatar look is generated / reused so consecutive
    # avatar blocks don't all share the same medium-close shot.
    framing = Column(String(20), default="MEDIUM", server_default="MEDIUM", nullable=False)

    # Acting block fields (populated when block_type='acting')
    acting_prompt = Column(Text, nullable=True)
    acting_first_frame_angle = Column(String(32), nullable=True)
    acting_last_frame_angle = Column(String(32), nullable=True)
    acting_video_r2_key = Column(String(255), nullable=True)
    acting_video_duration_seconds = Column(Float, nullable=True)

    # Generated-video block fields (category='generated_video'). The user may
    # provide a starting frame image and/or an ending frame image; the video
    # generator interpolates between them while following the script text as
    # the prompt. Either or both can be empty — the generator falls back to
    # text-only synthesis if both are absent.
    gen_video_first_frame_key = Column(Text, nullable=True)
    gen_video_last_frame_key = Column(Text, nullable=True)

    # Smart Cast metadata. Populated by the Smart Cast outline generator;
    # the editor reads these to render the right visual layout, choose a
    # transition, and remember which Pexels asset is attached so the user
    # can swap it. All nullable — legacy blocks are unaffected.
    hook_type = Column(String(40), nullable=True)
    background_type = Column(String(20), nullable=True)
    transition_in = Column(String(20), nullable=True)
    energy_level = Column(String(10), nullable=True)
    stock_media_query = Column(Text, nullable=True)
    stock_media_url = Column(Text, nullable=True)
    stock_media_thumbnail = Column(Text, nullable=True)
    stock_media_kind = Column(String(10), nullable=True)  # 'video' | 'photo'
    stock_media_pexels_id = Column(String(40), nullable=True)

    # Parallel media — visual content (stock photo/video) that runs ON TOP
    # of a voiceover or speaking block, rather than replacing the avatar.
    # For avatar_voiceover blocks the avatar is silent, so the timeline
    # mapping renders this list as a stock track ABOVE the voice audio.
    # Shape: [{"kind": "video"|"photo", "url": str, "thumbnail": str,
    #          "pexels_id": str|None, "source": str ("pexels"|"upload"),
    #          "start_offset_s": float (0=block start), "duration_s": float|None
    #          (None = until next item or block end)}]
    parallel_media = Column(JSON, nullable=True)

    # Body motion fields
    body_motion_start_look_id = Column(String(40), ForeignKey("avatar_looks.id"), nullable=True, index=True)
    body_motion_end_look_id = Column(String(40), ForeignKey("avatar_looks.id"), nullable=True, index=True)
    body_motion_prompt = Column(Text, nullable=True)
    # Per-block visual frame prompts. The Opus script-writer emits these
    # alongside body_motion_prompt: each describes what the START / END
    # frame should show (camera angle, pose, expression, scene). They are
    # the seed for the AI-generated start/end frames the user sees in the
    # carousel — the user can edit them and click Regenerate to get a new
    # frame. body_motion_prompt itself stays the verb (e.g. "walks toward
    # the camera") and is used by the body-motion video generator to
    # interpolate between the chosen start/end frame.
    body_motion_start_prompt = Column(Text, nullable=True)
    body_motion_end_prompt = Column(Text, nullable=True)

    # Avatar action frame prompts (avatar_action category — merged
    # avatar_motion + avatar_acting). Like body_motion_*_prompt but
    # generated against the avatar's face via FLUX Kontext for SCENE-SPECIFIC
    # frames (jungle, office, beach…). Renderer uses the resulting
    # AvatarLook rows (look_type=action_block_<block_id>_<kind>) as the
    # first/last frame for the I2V model so the action video is the
    # avatar — not a random person.
    action_start_prompt = Column(Text, nullable=True)
    action_end_prompt = Column(Text, nullable=True)

    # PIP engine: infinitetalk_rendered (default) or musetalk_live
    pip_engine = Column(String(30), default="infinitetalk_rendered", nullable=False)

    # voicing_mode (avatar_action / generated_video blocks):
    #   tts_dialogue   — TTS + lipsync (default; legacy behavior).
    #   motion_sfx_only — no TTS, no lipsync; the block plays silent and
    #                    the audio plan covers ambient/SFX.
    # ("prosody_only" was a third mode for a single non-verbal beat like
    #  [laugh]/[gasp] — removed with the rest of the prosody family, to
    #  return in a later phase. Legacy rows carrying it normalize to
    #  tts_dialogue.)
    # Persisted from the Opus outline; the renderer reads it in the
    # avatar_action branch.
    voicing_mode = Column(
        String(24), default="tts_dialogue", server_default="tts_dialogue", nullable=False,
    )

    # Layout template references
    layout_template_ids = Column(JSON, default=list)
    video_asset_id = Column(String, ForeignKey("product_assets.id"), nullable=True)
    image_asset_id = Column(String, ForeignKey("product_assets.id"), nullable=True)

    # Overlay
    overlay_type = Column(String, default=None)
    overlay_config = Column(JSON, default=None)

    # Generic per-block metadata bag for ad-hoc flags that don't justify their
    # own dedicated column. Currently used by the product carousel feature
    # (product_carousel, carousel_speed_seconds, carousel_transition,
    # carousel_asset_ids). NOTE: SQLAlchemy reserves `metadata` on Base, so the
    # python attribute is `block_metadata` while the underlying column is
    # `metadata`. The frontend sees it as `metadata` on the serialized block.
    block_metadata = Column("metadata", JSON, default=None, nullable=True)

    scene_image_key = Column(String, nullable=True)
    auto_basket_enabled = Column(Boolean, default=False)
    auto_basket_timeout = Column(Integer, default=60)
    mood = Column(String, nullable=True)
    key_points = Column(JSON, nullable=True)
    chat_rules = Column(JSON, nullable=True)
    deleted_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    cast = relationship("Cast", back_populates="blocks")
    product = relationship("Product")
    variants = relationship("Variant", back_populates="block", cascade="all, delete-orphan")
    avatar_look = relationship("AvatarLook", foreign_keys=[avatar_look_id])
    body_motion_start_look = relationship("AvatarLook", foreign_keys=[body_motion_start_look_id])
    body_motion_end_look = relationship("AvatarLook", foreign_keys=[body_motion_end_look_id])
