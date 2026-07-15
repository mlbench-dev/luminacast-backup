import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Integer, ForeignKey, Text, Boolean, Float, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class CloneTikTokScanStatus(str, enum.Enum):
    PENDING = "pending"
    SCRAPING = "scraping"
    ANALYZING = "analyzing"
    COMPLETE = "complete"
    FAILED = "failed"


class CloneTikTokScan(Base):
    __tablename__ = "clone_tiktok_scans"

    id = Column(String(40), primary_key=True)
    user_id = Column(String(40), ForeignKey("users.id"), index=True, nullable=False)
    tiktok_handle = Column(String(200), nullable=False)
    status = Column(
        Enum(CloneTikTokScanStatus, values_callable=lambda x: [e.value for e in x]),
        default=CloneTikTokScanStatus.PENDING,
        nullable=False,
    )
    videos_found = Column(Integer, default=0)
    videos_analyzed = Column(Integer, default=0)
    videos_with_full_body = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)

    videos = relationship("CloneTikTokVideo", back_populates="scan", cascade="all, delete-orphan")


class CloneTikTokVideo(Base):
    __tablename__ = "clone_tiktok_videos"

    id = Column(String(40), primary_key=True)
    scan_id = Column(String(40), ForeignKey("clone_tiktok_scans.id"), index=True, nullable=False)
    tiktok_video_id = Column(String(200), nullable=False)
    tiktok_url = Column(String(500), nullable=False)
    thumbnail_url = Column(String(500), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    download_url = Column(String(1000), nullable=True)  # signed R2 URL of low-res analysis copy
    has_full_body = Column(Boolean, default=False)
    full_body_segments = Column(JSON, default=list)  # [{start_ms, end_ms, confidence}]
    total_full_body_duration_ms = Column(Integer, default=0)
    first_full_body_frame_url = Column(String(500), nullable=True)  # R2 thumbnail
    analyzed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="pending")  # pending, analyzing, done, failed

    scan = relationship("CloneTikTokScan", back_populates="videos")
