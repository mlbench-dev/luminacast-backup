import enum
from datetime import datetime, timezone
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func


class VariantStatus(str, enum.Enum):
    PENDING = "PENDING"
    DRAFT = "draft"       # lowercase in DB
    GENERATING = "GENERATING"
    READY = "READY"
    COMPLETED = "completed"  # lowercase in DB
    FAILED = "FAILED"


class Variant(Base):
    __tablename__ = "variants"
    id = Column(String, primary_key=True)  # prefix: var_
    block_id = Column(String, ForeignKey("blocks.id"), index=True)
    status = Column(Enum(VariantStatus, values_callable=lambda x: [e.value for e in x]), default=VariantStatus.PENDING)

    variant_label = Column(String, default="A")
    variant_style = Column(String, default="")

    script_text = Column(Text, nullable=True)
    # Set ONLY when script_text itself changes (see the @validates hook
    # below) — deliberately separate from `updated_at`, which bumps on
    # ANY column write (captions, sfx timings, status, ...). The render
    # pipeline's stale-TTS check (tasks/cast_render.py
    # _ensure_fresh_tts_for_block) needs to ask specifically "was the
    # script edited after this audio was generated?" — using the
    # all-purpose `updated_at` for that question made it fire on the
    # normal generate-audio-then-generate-captions sequence, which always
    # writes captions in a second commit after the audio already exists.
    script_text_updated_at = Column(DateTime, nullable=True)
    motion_prompt = Column(Text, default="")
    estimated_duration_seconds = Column(Float, default=0)

    # Generated outputs
    # tts_r2_key is the 44.1 kHz mono MP3 192 kbps master, used as the
    # source of truth for the final compose audio remux (PR #64).
    # tts_lipsync_r2_key is the 16 kHz mono WAV consumed by the lipsync
    # engine (PR #65). Both share the same broadcast-quality EQ chain;
    # only sample rate / codec differ.
    tts_r2_key = Column(String, default="")
    tts_lipsync_r2_key = Column(Text, nullable=True)
    tts_duration_seconds = Column(Float, default=0)
    audio_key = Column(String, nullable=True)
    video_key = Column(String, nullable=True)
    video_r2_key = Column(String, default="")
    video_r2_url = Column(String, default="")
    final_video_key = Column(String(500), nullable=True)  # composited output from Twick timeline

    # Caption alignment (Whisper word-level timestamps)
    caption_words = Column(JSON, nullable=True)     # [{word, start, end, probability}, ...]
    caption_segments = Column(JSON, nullable=True)   # [{start, end, text, words: [...]}, ...]
    word_timestamps = Column(JSON, nullable=True)  # [{word, start, end}, ...] from WhisperX on TTS audio

    # Sound-effect markers extracted from script_text BEFORE marker-stripping,
    # then resolved to absolute timings once caption_words land (regression 4).
    sfx_markers = Column(JSON, nullable=True)   # [{name, char_offset, word_index}, ...]
    sfx_timings = Column(JSON, nullable=True)   # [{name, start_s}, ...]

    duration_seconds = Column(Float, nullable=True)
    weight = Column(Float, default=1.0)
    times_played = Column(Integer, default=0)
    purchases_during = Column(Integer, default=0)
    performance_score = Column(Float, nullable=True)
    generation_error = Column(String, nullable=True)
    composition_warnings = Column(JSON, default=list, nullable=True)  # list of {step: str, error: str}
    retry_count = Column(Integer, default=0)
    runpod_job_id = Column(String, nullable=True, index=True)  # RunPod job ID for webhook matching
    is_active = Column(Boolean, default=True, nullable=False, server_default="true", index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    block = relationship("Block", back_populates="variants")

    @validates("script_text")
    def _touch_script_text_updated_at(self, key, value):
        # SQLAlchemy validators only run on an explicit Python assignment
        # (`variant.script_text = ...`), never while hydrating a row from
        # a SELECT — so this fires exactly on real edits, including the
        # very first one when a variant is created with a script.
        if value != self.script_text:
            # script_text_updated_at is DateTime (no timezone) like every
            # other timestamp column here — asyncpg rejects a tz-aware
            # value against a "timestamp without time zone" column outright
            # (every script edit anywhere would fail to commit), so this
            # strips tzinfo the same way routers/casts/variants.py already
            # does for audio_stale_since.
            self.script_text_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return value
