from datetime import datetime
from database import Base
from sqlalchemy import Column, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.sql import func


class VoiceCorpusEntry(Base):
    __tablename__ = "creator_voice_corpus"

    id = Column(String(40), primary_key=True)
    avatar_id = Column(String(40), ForeignKey("avatars.id"), nullable=False, index=True)
    source_type = Column(String(20), nullable=False)  # "uploaded" or "tiktok_scrape"
    source_url = Column(String(1000), nullable=True)
    audio_r2_key = Column(String(500), nullable=True)
    transcript = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    status = Column(String(20), default="pending", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
