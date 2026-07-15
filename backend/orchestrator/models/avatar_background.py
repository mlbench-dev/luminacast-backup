"""Phase 2: Create AvatarBackground model"""
from database import Base
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Enum as SAEnum
from sqlalchemy.sql import func
import enum


class BackgroundSource(str, enum.Enum):
    UPLOAD = "upload"
    AI_GENERATED = "ai_generated"
    SYSTEM = "system"


class AvatarBackground(Base):
    __tablename__ = "avatar_backgrounds"

    id = Column(String, primary_key=True)  # prefix: bg_
    avatar_id = Column(String, ForeignKey("avatars.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)

    name = Column(String, nullable=False, default="Untitled Background")
    r2_key = Column(String, nullable=False)
    thumbnail_r2_key = Column(String, default="")

    source = Column(SAEnum(BackgroundSource), default=BackgroundSource.UPLOAD, index=True)
    generation_prompt = Column(String, default="")

    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    file_size_bytes = Column(Integer, default=0)

    position = Column(Integer, default=0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)
