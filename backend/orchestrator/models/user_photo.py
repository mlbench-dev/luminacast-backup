"""User Photo Asset model — uploaded photos for use in casts."""
from database import Base
from sqlalchemy import Column, String, DateTime, Integer, BigInteger, ForeignKey
from sqlalchemy.sql import func


class UserPhotoAsset(Base):
    __tablename__ = "user_photo_assets"
    id = Column(String(40), primary_key=True)
    user_id = Column(String(40), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200))
    r2_key = Column(String(500), nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    file_size_bytes = Column(BigInteger)
    content_type = Column(String(100))
    original_filename = Column(String(300))
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    deleted_at = Column(DateTime)
