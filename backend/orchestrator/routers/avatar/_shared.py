"""Shared helpers used across multiple avatar route modules."""

from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy import update as sa_update
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User, TeamRole
from models.avatar import Avatar, AvatarType, AvatarStatus, BodyShotSet
from models.avatar_look import AvatarLook
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.fish_audio import get_fish_audio_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)
import re

logger = logging.getLogger(__name__)


def normalize_tiktok_input(raw: str) -> str:
    """Normalize any TikTok input to a full profile URL.

    Accepts:
      @username, username, tiktok.com/@user, www.tiktok.com/@user,
      http(s)://tiktok.com/@user, full URL with query params, etc.
    """
    val = raw.strip()
    if not val:
        return val

    # Already a full URL
    if val.startswith("https://www.tiktok.com/") or val.startswith("https://tiktok.com/"):
        return val
    if val.startswith("http://www.tiktok.com/") or val.startswith("http://tiktok.com/"):
        return val.replace("http://", "https://", 1)

    # Partial URL without scheme: www.tiktok.com/@user or tiktok.com/@user
    if re.match(r"^(www\.)?tiktok\.com/", val):
        return f"https://{val}" if val.startswith("www.") else f"https://www.{val}"

    # Just a handle: @username or username
    handle = val.lstrip("@").strip()
    # Remove any trailing path/query if someone pasted @user/video/123
    handle = handle.split("/")[0].split("?")[0]
    # TikTok handles: letters, numbers, underscores, dots only — no spaces
    if handle and re.match(r'^[\w.]+$', handle):
        return f"https://www.tiktok.com/@{handle}"

    return val

def _r2_key_to_url(key: str) -> str:
    """Convert an R2 key to a full CDN URL. Handles legacy full URLs gracefully."""
    if not key:
        return key
    if key.startswith("http://") or key.startswith("https://"):
        return key  # Already a full URL (legacy data)
    from services.r2_storage import get_r2_storage_service
    return get_r2_storage_service().get_public_url(key)

class AvatarResponse(BaseModel):
    id: str
    type: str
    status: str
    name: Optional[str] = None
    description: Optional[str] = None
    voice_style: Optional[str] = None
    background: Optional[str] = None
    camera_position: Optional[str] = None
    style: Optional[str] = None
    ai_model: Optional[str] = None
    appearance_prompt: Optional[str] = None
    face_ref_key: Optional[str] = None
    voice_id: Optional[str] = None
    test_script: Optional[str] = None
    test_audio_key: Optional[str] = None
    test_video_key: Optional[str] = None
    test_video_url: Optional[str] = None
    face_image_url: Optional[str] = None
    persona_profile: Optional[dict] = None
    style_dna: Optional[dict] = None
    candidate_frames: Optional[list[str]] = None
    tiktok_source_url: Optional[str] = None
    progress_step: Optional[str] = None
    progress_percent: Optional[float] = 0
    voice_clone_progress: Optional[int] = None
    regeneration_count: int = 0
    voice_corpus_count: int = 0
    target_audience: Optional[dict] = None
    body_description: Optional[str] = None
    locked_test_script: Optional[str] = None
    preview_video_url: Optional[str] = None
    gender: Optional[str] = None
    style_preset: Optional[str] = None
    wizard_step: Optional[str] = None
    detected_language: Optional[str] = None
    # PR #65: clip-on lavalier vs phone-mic toggle. Default false = phone mic.
    clip_mic_enabled: bool = False
    created_at: Optional[str] = None
    render_status: Optional[dict] = None

    model_config = {"from_attributes": True}

def _avatar_to_response(avatar: Avatar, voice_corpus_count: int = 0) -> AvatarResponse:
    # Use public CDN URLs — browser loads directly from Cloudflare edge
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    test_video_url = r2.get_public_url(avatar.test_video_key) if avatar.test_video_key else None

    # Convert R2 keys to CDN URLs at serialization time (B-068)
    candidate_frames = None
    if avatar.candidate_frames:
        candidate_frames = [_r2_key_to_url(f) for f in avatar.candidate_frames]

    return AvatarResponse(
        id=avatar.id,
        type=avatar.type.value if avatar.type else "digital",
        status=avatar.status.value if avatar.status else "processing",
        name=avatar.name,
        description=avatar.description,
        voice_style=avatar.voice_style,
        background=avatar.background,
        camera_position=avatar.camera_position,
        style=avatar.style,
        ai_model=avatar.ai_model,
        appearance_prompt=avatar.appearance_prompt,
        face_ref_key=avatar.face_ref_key,
        voice_id=avatar.voice_id,
        test_script=avatar.test_script,
        test_audio_key=avatar.test_audio_key,
        test_video_key=avatar.test_video_key,
        test_video_url=test_video_url,
        # cache_bust=True: when the user picks a new face for an existing
        # avatar, ai_select_face overwrites the same R2 key (face_ref.jpg).
        # Without a cache buster, the CDN serves the previous image and the
        # voice-creation step shows the old face.
        face_image_url=r2.get_public_url(avatar.face_ref_key, cache_bust=True) if avatar.face_ref_key else None,
        persona_profile=avatar.persona_profile,
        style_dna=avatar.style_dna,
        candidate_frames=candidate_frames,
        tiktok_source_url=avatar.tiktok_source_url,
        progress_step=avatar.progress_step,
        progress_percent=avatar.progress_percent or 0,
        voice_clone_progress=avatar.voice_clone_progress,
        regeneration_count=avatar.regeneration_count or 0,
        voice_corpus_count=voice_corpus_count,
        target_audience=avatar.target_audience,
        body_description=avatar.body_description,
        locked_test_script=avatar.locked_test_script,
        preview_video_url=r2.get_public_url(avatar.preview_video_key) if avatar.preview_video_key else None,
        gender=avatar.gender,
        style_preset=avatar.style_preset,
        wizard_step=avatar.wizard_step,
        detected_language=avatar.detected_language,
        clip_mic_enabled=bool(getattr(avatar, "clip_mic_enabled", False)),
        created_at=avatar.created_at.isoformat() if avatar.created_at else None,
    )

_BODY_MOTION_POSES = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"]

_BODY_MOTION_POSE_LABELS = {
    "front": "Front",
    "three_quarter_left": "3/4 Left",
    "three_quarter_right": "3/4 Right",
    "profile_left": "Profile Left",
    "profile_right": "Profile Right",
    "back": "Back",
}

async def _seed_body_motion_looks_from_body_shot_set(db: AsyncSession, avatar_id: str) -> int:
    """Create body_motion AvatarLook rows from the most recent completed
    BodyShotSet.

    BodyShotSet (the AI-avatar wizard's "Shots" step) and AvatarLook
    (everything else — Edit Avatar's Body Motion tab, the cast builder's
    body-motion picker, try-on's required "front" pose lookup) are two
    separate tables. This seeding previously only ran inside approve_avatar,
    so an avatar whose body shots were generated but never explicitly
    approved (or approved via a different path) showed an empty Body Motion
    tab, forcing the user to regenerate photos that already existed. Calling
    this right when BodyShotSet generation completes — not just on approval
    — closes that gap; it's still called from approve_avatar too since it's
    idempotent (skips any pose that already has a ready/generating/pending
    AvatarLook row) and cheap to no-op on a second call.
    """
    seeded_count = 0
    try:
        bss_result = await db.execute(
            select(BodyShotSet)
            .where(BodyShotSet.avatar_id == avatar_id, BodyShotSet.status == "completed")
            .order_by(BodyShotSet.created_at.desc())
            .limit(1)
        )
        body_shot_set = bss_result.scalars().first()
        if body_shot_set and body_shot_set.angles:
            angles = body_shot_set.angles
            for pose in _BODY_MOTION_POSES:
                pose_key = angles.get(pose)
                if not pose_key:
                    continue
                existing_bm = await db.execute(
                    select(AvatarLook).where(
                        AvatarLook.avatar_id == avatar_id,
                        AvatarLook.pose_angle == pose,
                        AvatarLook.look_type == "body_motion",
                        AvatarLook.status.in_(["ready", "generating", "pending"]),
                    )
                )
                if existing_bm.scalars().first():
                    continue
                label = _BODY_MOTION_POSE_LABELS[pose]
                db.add(AvatarLook(
                    id=f"al_{uuid.uuid4().hex[:12]}",
                    avatar_id=avatar_id,
                    name=f"AI: {label}",
                    face_ref_key=pose_key,
                    background_prompt=f"Body motion pose: {pose}",
                    is_default=False,
                    is_original=False,
                    status="ready",
                    look_type="body_motion",
                    pose_angle=pose,
                ))
                seeded_count += 1
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Failed to seed body_motion looks for avatar %s: %s", avatar_id, e)
    return seeded_count
