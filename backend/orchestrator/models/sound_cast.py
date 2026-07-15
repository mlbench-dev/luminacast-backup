"""Sound Cast and Music Track models for the MUSIC tab."""

import enum

from sqlalchemy import Column, String, DateTime, Integer, Float, JSON, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class SoundCastStatus(str, enum.Enum):
    DRAFT = "draft"
    TRAINING_PENDING = "training_pending"
    TRAINING_IN_PROGRESS = "training_in_progress"
    TRAINED = "trained"
    FAILED = "failed"


class SoundCast(Base):
    __tablename__ = "sound_casts"

    id = Column(String, primary_key=True)  # prefix: sc_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")

    status = Column(SAEnum(SoundCastStatus, values_callable=lambda e: [x.value for x in e], create_type=False), default=SoundCastStatus.DRAFT, index=True)

    # Training
    lora_r2_key = Column(String, default="")
    training_audio_keys = Column(JSON, default=list)
    training_prompts = Column(JSON, default=list)
    training_steps = Column(Integer, default=2400)
    training_loss = Column(Float, default=0.0)
    training_started_at = Column(DateTime, nullable=True)
    training_completed_at = Column(DateTime, nullable=True)
    training_error = Column(String, default="")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)

    tracks = relationship("MusicTrack", back_populates="sound_cast", cascade="all, delete-orphan")


class MusicTrackStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class MusicTrack(Base):
    __tablename__ = "music_tracks"

    id = Column(String, primary_key=True)  # prefix: mt_
    sound_cast_id = Column(String, ForeignKey("sound_casts.id", ondelete="CASCADE"), index=True)
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)

    name = Column(String, nullable=False)
    prompt = Column(String, nullable=False)
    lyrics = Column(String, default="")
    duration_seconds = Column(Float, default=60.0)
    seed = Column(Integer, default=-1)
    guidance_scale = Column(Float, default=15.0)
    inference_steps = Column(Integer, default=60)
    scheduler_type = Column(String, default="euler")

    status = Column(SAEnum(MusicTrackStatus, values_callable=lambda e: [x.value for x in e], create_type=False), default=MusicTrackStatus.PENDING, index=True)
    audio_r2_key = Column(String, default="")
    actual_duration_seconds = Column(Float, default=0.0)
    generation_time_seconds = Column(Float, default=0.0)
    generation_error = Column(String, default="")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)

    sound_cast = relationship("SoundCast", back_populates="tracks")
