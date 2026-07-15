import uuid
from database import Base
from sqlalchemy import Column, String, Integer, Text, BigInteger, DateTime, DECIMAL, ForeignKey
from sqlalchemy.sql import func


class AIGeneratedPhoto(Base):
    __tablename__ = "ai_generated_photos"
    id = Column(String(40), primary_key=True, default=lambda: f"gp_{uuid.uuid4().hex[:12]}")
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    r2_key = Column(String(512), nullable=False)
    prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    seed = Column(BigInteger)
    engine_used = Column(String(64), nullable=False)
    tier = Column(Integer, nullable=False)
    model_variant = Column(String(64))
    cost_usd = Column(DECIMAL(10, 4))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
