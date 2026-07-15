import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Integer, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class RenderJobState(str, enum.Enum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    STALLED = "STALLED"
    FAILED = "FAILED"
    ENDPOINT_DOWN = "ENDPOINT_DOWN"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"


class RenderJobType(str, enum.Enum):
    AVATAR_PREVIEW = "avatar_preview"
    CAST_VARIANT = "cast_variant"
    BODY_SHOTS = "body_shots"
    CLONE_PREVIEW = "clone_preview"
    # Clone pipeline steps (F1)
    CLONE_UPLOAD = "clone_upload"
    CLONE_FACE_EXTRACT = "clone_face_extract"
    CLONE_VOICE_EXTRACT = "clone_voice_extract"
    CLONE_VOICE_TRAINING = "clone_voice_training"
    CLONE_PREVIEW_RENDER = "clone_preview_render"
    # AI pipeline steps (F1)
    AI_FACE_GENERATION = "ai_face_generation"
    AI_FACE_SELECTION = "ai_face_selection"
    AI_VOICE_GENERATION = "ai_voice_generation"
    AI_VOICE_SELECTION = "ai_voice_selection"
    AI_BODY_SHOTS = "ai_body_shots"
    AI_PREVIEW_RENDER = "ai_preview_render"
    # Acting video generation (Phase D)
    ACTING_VIDEO_GENERATION = "acting_video_generation"


class RenderProvider(str, enum.Enum):
    RUNPOD_INFINITETALK = "runpod_infinitetalk"
    RUNPOD_MUSETALK = "runpod_musetalk"
    FAL_FLUX_KONTEXT = "fal_flux_kontext"
    FAL_KLING = "fal_kling"
    HOSTKEY_LOCAL = "hostkey_local"


class RenderJob(Base):
    __tablename__ = "render_jobs"

    id = Column(String(40), primary_key=True)  # prefix: rj_
    job_type = Column(
        Enum(RenderJobType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    provider = Column(
        Enum(RenderProvider, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    external_job_id = Column(String(200), nullable=True)  # RunPod job ID, fal request ID
    avatar_id = Column(String(40), ForeignKey("avatars.id"), nullable=True, index=True)
    cast_id = Column(String(40), ForeignKey("casts.id"), nullable=True, index=True)
    variant_id = Column(String(40), ForeignKey("variants.id"), nullable=True, index=True)
    state = Column(
        Enum(RenderJobState, values_callable=lambda x: [e.value for e in x]),
        default=RenderJobState.QUEUED,
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    queued_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    last_status_check_at = Column(DateTime, nullable=True)
    last_state_change_at = Column(DateTime, nullable=True)
    progress_percent = Column(Integer, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)  # request payload, retry count, etc
