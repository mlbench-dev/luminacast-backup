from database import Base
from sqlalchemy import Column, String, DateTime, Boolean, Float, ForeignKey
from sqlalchemy.sql import func


class VoiceModel(Base):
    __tablename__ = "voice_models"
    id = Column(String, primary_key=True)               # prefix: vm_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    avatar_id = Column(String, ForeignKey("avatars.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    r2_model_key = Column(String, nullable=False)       # R2 key for model checkpoint
    source_audio_key = Column(String, nullable=True)    # R2 key for training audio
    source_platform = Column(String, nullable=True)     # tiktok | instagram | youtube | upload
    duration_trained_seconds = Column(Float, nullable=True)
    quality_score = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)
