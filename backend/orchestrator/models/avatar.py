import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class AvatarType(str, enum.Enum):
    CLONE = "CLONE"
    DIGITAL = "DIGITAL"


class AvatarStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    CANDIDATES_READY = "CANDIDATES_READY"
    FACE_CANDIDATES_READY = "FACE_CANDIDATES_READY"
    READY = "READY"
    APPROVED = "APPROVED"
    FAILED = "FAILED"


class AvatarPhase(str, enum.Enum):
    IMAGE = "image"           # detecting/scoring faces, user hasn't picked yet
    VOICE = "voice"           # face picked, voice cloning in progress
    RENDER = "render"         # voice done, rendering test video
    READY = "ready"           # all done
    FAILED = "failed"


class Avatar(Base):
    __tablename__ = "avatars"
    id = Column(String, primary_key=True)  # prefix: avt_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    type = Column(Enum(AvatarType))
    status = Column(Enum(AvatarStatus), default=AvatarStatus.PROCESSING, index=True)
    name = Column(String, nullable=True)
    description = Column(String, nullable=True)
    voice_style = Column(String, nullable=True)
    background = Column(String, nullable=True)
    camera_position = Column(String, nullable=True)
    style = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    appearance_prompt = Column(String, nullable=True)    # LLM-generated visual prompt
    face_ref_key = Column(String, nullable=True)         # R2 key for face image
    voice_sample_key = Column(String, nullable=True)     # R2 key for user-uploaded voice sample
    video_ref_key = Column(String, nullable=True)        # R2 key for clone reference video
    voice_id = Column(String, nullable=True)             # Fish Audio voice ID
    test_script = Column(String, nullable=True)          # Text the avatar speaks in test video
    test_audio_key = Column(String, nullable=True)       # R2 key for test TTS audio
    test_video_key = Column(String, nullable=True)       # R2 key for InfiniteTalk test video
    persona_profile = Column(JSON, nullable=True)
    candidate_frames = Column(JSON, nullable=True)     # Array of R2 keys for face frame candidates
    face_candidates = Column(JSON, nullable=True)      # Array of AI-generated face URLs (Face step, DIGITAL avatars) — lets a page refresh restore the batch instead of regenerating
    selected_face_url = Column(String, nullable=True)  # Which face_candidates entry is highlighted, before "Continue to voice" commits it to face_ref_key
    edited_face_versions = Column(JSON, nullable=True) # Accumulated /ai/edit-face results for the currently selected base face — cleared when a different base face is picked
    # Voice step (DIGITAL avatars) — mirrors the face_candidates pattern so a
    # refresh restores the in-progress voice pick instead of regenerating the
    # description and resetting language/accent to defaults. Distinct from
    # voice_style (legacy enum-driven clone-pipeline field) and test_script /
    # locked_test_script (only written once the Voice step is locked).
    voice_description = Column(Text, nullable=True)
    voice_test_speech = Column(Text, nullable=True)
    voice_language = Column(String(20), nullable=True)
    voice_accent = Column(String(20), nullable=True)
    voice_desc_overridden = Column(Boolean, default=False, nullable=False, server_default=text("false"))
    voice_previews = Column(JSON, nullable=True)              # Array of {preview_id, audio_url, index} — R2-hosted, safe to persist
    selected_voice_preview_idx = Column(Integer, nullable=True)
    candidate_scores = Column(JSON, nullable=True)     # Array of floats — real scores from MediaPipe+Gemini pipeline
    tiktok_source_url = Column(String, nullable=True)
    progress_step = Column(String, nullable=True)
    progress_percent = Column(Float, default=0)
    voice_clone_progress = Column(Integer, nullable=True)  # Voice pipeline progress 0-100
    source_platform = Column(String, nullable=True)  # tiktok | instagram | youtube | twitter | linkedin | twitch | upload
    runpod_job_id = Column(String, nullable=True)          # RunPod job ID for webhook-based test video rendering
    active_phase = Column(Enum(AvatarPhase, values_callable=lambda e: [m.value for m in e]), default=AvatarPhase.IMAGE, nullable=False, server_default="image", index=True)
    style_preset = Column(String(50), nullable=True, server_default="studio")
    imperfections = Column(JSON, nullable=True)  # "Make It Real" chip ids from AI Avatar setup
    gender = Column(String(20), nullable=True)
    regeneration_count = Column(Integer, default=0, server_default="0", nullable=False)
    # PR #65: clip-mic toggle. When ON the TTS pipeline (a) appends a
    # lavalier-mic style suffix to the voice description sent to the
    # model, and (b) switches the post-process EQ chain to the lav
    # profile (warm proximity, tighter compand). OFF = phone-mic default.
    clip_mic_enabled = Column(
        Boolean, default=False, nullable=False,
        server_default=text("false"),
    )
    target_audience = Column(JSON, nullable=True)
    body_description = Column(Text, nullable=True)
    locked_test_script = Column(Text, nullable=True)
    preview_video_key = Column(String, nullable=True)
    wizard_step = Column(String(30), nullable=True)  # Phase 2: tracks clone/AI flow step for draft resume
    detected_language = Column(String(10), nullable=True)  # Auto-detected via langdetect
    # Style DNA — full editing-style profile cloned from creator videos.
    # Includes pacing, b-roll ratio, caption preset, signature phrases, etc.
    # Voice clone id lives in the existing `voice_id` column; this JSON
    # is the directorial half (read by cast_generator at production time).
    style_dna = Column(JSONB, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="avatars")
    looks = relationship("AvatarLook", back_populates="avatar", cascade="all, delete-orphan")
    body_shot_sets = relationship("BodyShotSet", back_populates="avatar", cascade="all, delete-orphan")


class BodyShotSet(Base):
    __tablename__ = "body_shot_sets"
    id = Column(String, primary_key=True)
    avatar_id = Column(String, ForeignKey("avatars.id"), index=True, nullable=False)
    front_shot_key = Column(String, nullable=True)
    angles = Column(JSON, nullable=True)
    description_used = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    approved_at = Column(DateTime, nullable=True)

    # Async job tracking. Generation takes ~60s and used to be a blocking HTTP
    # request, which Cloudflare killed at the 100s edge timeout. Now the POST
    # creates the row immediately with status='running' and returns; a background
    # task fills in the angles and flips status to 'completed' or 'failed'.
    status = Column(String, nullable=True)  # 'running' | 'completed' | 'failed'
    error_message = Column(Text, nullable=True)
    canonical_key = Column(String, nullable=True)
    validation = Column(JSON, nullable=True)

    avatar = relationship("Avatar", back_populates="body_shot_sets")
