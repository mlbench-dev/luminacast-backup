import uuid
from database import Base
from sqlalchemy import Column, String, Integer, Text, BigInteger, DateTime, DECIMAL, ForeignKey
from sqlalchemy.sql import func


class AIGeneratedVideo(Base):
    __tablename__ = "ai_generated_videos"
    id = Column(String(40), primary_key=True, default=lambda: f"gv_{uuid.uuid4().hex[:12]}")
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    r2_key = Column(String(512), nullable=False)
    thumbnail_r2_key = Column(String(512))
    prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text)
    engine_used = Column(String(64), nullable=False)
    mode = Column(String(32), nullable=False)
    duration_seconds = Column(Integer, nullable=False)
    aspect_ratio = Column(String(16), nullable=False)
    camera_preset = Column(String(64))
    reference_image_r2_key = Column(String(512))
    seed = Column(BigInteger)
    cost_usd = Column(DECIMAL(10, 4))
    batch_id = Column(String(64))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
