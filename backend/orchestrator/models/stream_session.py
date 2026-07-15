import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class StreamSession(Base):
    __tablename__ = "stream_sessions"
    id = Column(String, primary_key=True)  # prefix: ses_
    cast_id = Column(String, ForeignKey("casts.id"), index=True)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    started_at = Column(DateTime, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)
    duration_minutes = Column(Float, default=0)
    peak_viewers = Column(Integer, default=0)
    total_purchases = Column(Integer, default=0)
    total_gmv = Column(Float, default=0)
    streaming_cost_cents = Column(Integer, default=0)
    analytics_r2_key = Column(String, nullable=True)
    afk_events = Column(Integer, default=0)
    celery_task_id = Column(String, nullable=True)
    rtmp_url = Column(String, nullable=True)
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    cast = relationship("Cast", back_populates="stream_sessions")
    user = relationship("User")
    chat_messages = relationship("ChatMessage", back_populates="session")
