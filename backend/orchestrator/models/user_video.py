"""User Video Asset model — uploaded videos for use in casts."""
from database import Base
from sqlalchemy import Column, String, DateTime, Float, Integer, BigInteger, ForeignKey
from sqlalchemy.sql import func


class UserVideoAsset(Base):
    __tablename__ = "user_video_assets"
    id = Column(String(40), primary_key=True)
    user_id = Column(String(40), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200))
    r2_key = Column(String(500), nullable=False)
    thumbnail_r2_key = Column(String(500))
    duration_seconds = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    file_size_bytes = Column(BigInteger)
    original_filename = Column(String(300))
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    deleted_at = Column(DateTime)
