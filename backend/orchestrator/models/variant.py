import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship
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
