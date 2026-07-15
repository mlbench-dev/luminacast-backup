from database import Base
from sqlalchemy import Column, String, DateTime, Boolean, JSON, Float, Integer, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class Channel(Base):
    __tablename__ = "channels"

    id = Column(String, primary_key=True)                  # prefix: ch_
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Platform
    platform = Column(String, nullable=False)              # "tiktok" | "instagram" | "youtube"
    handle = Column(String, nullable=False)                # "@lorigreiner"
    platform_user_id = Column(String, default="")
    display_name = Column(String, default="")
    bio = Column(Text, default="")
    profile_image_key = Column(String, default="")         # R2 key

    # Stats (updated on each index)
    followers_count = Column(Integer, default=0)
    video_count = Column(Integer, default=0)
    avg_views = Column(Integer, default=0)

    # Streaming credentials
    stream_key = Column(String, default="")
    stream_url = Column(String, default="")

    # Content indexing
    index_status = Column(String, default="pending")       # pending | indexing | completed | failed
    indexed_video_count = Column(Integer, default=0)
    index_target_count = Column(Integer, default=50)
    relevant_video_count = Column(Integer, default=0)
    last_indexed_at = Column(DateTime, nullable=True)

    # Voice profile
    voice_profile = Column(JSON, default=None)

    # Speaker embedding
    speaker_embedding_key = Column(String, default="")

    status = Column(String, default="active")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    transcripts = relationship("ChannelTranscript", back_populates="channel",
                               cascade="all, delete-orphan")
    user = relationship("User", backref="channels")

    __table_args__ = (
        Index("ix_channels_user_platform", "user_id", "platform", "handle", unique=True),
    )


class ChannelTranscript(Base):
    __tablename__ = "channel_transcripts"

    id = Column(String, primary_key=True)                  # prefix: ct_
    channel_id = Column(String, ForeignKey("channels.id", ondelete="CASCADE"),
                        nullable=False, index=True)

    # Video reference
    video_url = Column(String, default="")
    video_title = Column(String, default="")
    video_duration_seconds = Column(Float, default=0)
    video_views = Column(Integer, default=0)
    video_posted_at = Column(DateTime, nullable=True)

    # Relevance filtering
    is_relevant = Column(Boolean, default=True)
    relevance_reason = Column(String, default="")
    speech_ratio = Column(Float, default=0)
    has_main_speaker = Column(Boolean, default=True)

    # Transcription
    transcript_text = Column(Text, default="")
    transcript_language = Column(String, default="en")
    word_count = Column(Integer, default=0)

    # Per-video analysis
    analysis = Column(JSON, default=None)

    status = Column(String, default="pending")             # pending | transcribing | completed | filtered_out | failed
    created_at = Column(DateTime, server_default=func.now())

    channel = relationship("Channel", back_populates="transcripts")
