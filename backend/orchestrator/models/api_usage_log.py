from database import Base
from sqlalchemy import Column, String, DateTime, Boolean, Float, Integer, ForeignKey
from sqlalchemy.sql import func


class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"
    id = Column(String, primary_key=True)              # prefix: usage_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    avatar_id = Column(String, ForeignKey("avatars.id"), nullable=True, index=True)
    cast_id = Column(String, ForeignKey("casts.id"), nullable=True, index=True)
    service = Column(String, nullable=False, index=True)  # bs_roformer | fish_speech | infinitetalk | flux_kontext | gemini_vision | openrouter_llm | apify_scrape | pyannote
    operation = Column(String, nullable=False)          # voice_clone | tts_generate | face_edit | vocal_isolation | face_extraction | video_generation | scrape
    duration_seconds = Column(Float, nullable=True)     # GPU/processing time
    input_size_bytes = Column(Integer, nullable=True)
    output_size_bytes = Column(Integer, nullable=True)
    cost_cents = Column(Integer, default=0)             # our actual cost
    runpod_job_id = Column(String, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
