"""Cost tracking for external API calls and GPU worker operations."""
from database import Base
from sqlalchemy import Column, String, DateTime, Integer, Numeric, ForeignKey, JSON
from sqlalchemy.sql import func


class GenerationCost(Base):
    __tablename__ = "generation_costs"

    id = Column(String(40), primary_key=True)
    user_id = Column(String(40), ForeignKey("users.id"), index=True, nullable=True)
    avatar_id = Column(String(40), ForeignKey("avatars.id"), index=True, nullable=True)
    cast_id = Column(String(40), ForeignKey("casts.id"), index=True, nullable=True)
    block_id = Column(String(40), nullable=True, index=True)
    engine = Column(String(50), nullable=False)
    operation = Column(String(50), nullable=False)
    cost_usd = Column(Numeric(10, 4), nullable=False)
    quantity = Column(Integer, default=1)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
