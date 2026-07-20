from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User
from models.avatar import Avatar, AvatarType, AvatarStatus, BodyShotSet
from models.avatar_look import AvatarLook
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.fish_audio import get_fish_audio_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/avatar", tags=["avatar"])


# ── Request schemas ──

import re

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


class CloneFromTikTokRequest(BaseModel):
    tiktok_url: Optional[str] = ""
    name: Optional[str] = ""
    test_script: Optional[str] = ""
    consent_confirmed: bool = False


class GenerateDigitalRequest(BaseModel):
    name: Optional[str] = ""
    description: Optional[str] = ""
    voice_style: Optional[str] = "energetic"
    background: Optional[str] = "studio"
    camera_position: Optional[str] = "waist_up"
    style: Optional[str] = "photorealistic"
    ai_model: Optional[str] = "meta-llama/llama-3-70b-instruct"
    persona_preset: Optional[str] = "energetic_beauty"


# ── Response schemas ──

class RegenerateRequest(BaseModel):
    test_script: Optional[str] = None  # Custom text for the avatar to speak


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


class SelectFrameRequest(BaseModel):
    frame_url: str


class CandidatesResponse(BaseModel):
    avatar_id: str
    status: str
    candidate_frames: list[str]


class AvatarListResponse(BaseModel):
    avatars: list[AvatarResponse]
    total: int


# ── New clone-flow-rebuild schemas ──

class FetchVideosRequest(BaseModel):
    tiktok_url: str = ""
    name: str = ""
    page: int = 1
    per_page: int = 16

class FetchedVideo(BaseModel):
    thumb_url: str          # R2 CDN URL (proxied from TikTok cover image)
    video_url: str          # R2 CDN URL (populated after download) or empty
    web_video_url: str = "" # TikTok page URL for yt-dlp download
    duration_seconds: float
    video_r2_key: str = "" # R2 key (populated after download)
    description: str = ""
    views: int = 0
    has_face: Optional[bool] = False
    face_confidence: float = 0.0
    likes: int = 0
    create_time: Optional[str] = None
    comments: Optional[list[dict]] = None

class FetchVideosResponse(BaseModel):
    videos: list[FetchedVideo]
    total: int = 0
    page: int = 1
    per_page: int = 16
    total_pages: int = 0

class ProcessSegmentRequest(BaseModel):
    video_r2_key: str
    start_seconds: float
    end_seconds: float

class EditFrameRequest(BaseModel):
    frame_url: str
    instructions: str = ""

class EditFrameResponse(BaseModel):
    original_url: str
    edited_url: str

class FaceCandidateItem(BaseModel):
    url: str
    score: float

class FaceCandidatesResponse(BaseModel):
    avatar_id: str
    status: str
    candidates: list[FaceCandidateItem]
    voice_clone_progress: Optional[int] = None
    progress_step: Optional[str] = None


def _r2_key_to_url(key: str) -> str:
    """Convert an R2 key to a full CDN URL. Handles legacy full URLs gracefully."""
    if not key:
        return key
    if key.startswith("http://") or key.startswith("https://"):
        return key  # Already a full URL (legacy data)
    from services.r2_storage import get_r2_storage_service
    return get_r2_storage_service().get_public_url(key)


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


# ── Endpoints ──

@router.get("/list", response_model=AvatarListResponse)
async def list_avatars(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
):
    """List all avatars for the current user. Optional status filter (comma-separated)."""
    query = (
        select(Avatar)
        .where(Avatar.user_id == user.id)
        .where(Avatar.id != "default")
        .order_by(Avatar.created_at.desc())
    )
    if status:
        allowed = [s.strip().upper() for s in status.split(",")]
        query = query.where(Avatar.status.in_(allowed))
    else:
        # By default, exclude FAILED avatars to keep the list clean
        query = query.where(Avatar.status != AvatarStatus.FAILED)
    result = await db.execute(query)
    avatars = result.scalars().all()

    # Auto-fail avatars stuck in PROCESSING for >30 minutes
    stale_cutoff = datetime.utcnow() - timedelta(minutes=30)
    for a in avatars:
        if a.status == AvatarStatus.PROCESSING and (a.updated_at or a.created_at) < stale_cutoff:
            a.status = AvatarStatus.FAILED
            a.progress_step = "Generation timed out — please retry"
    await db.commit()

    # Compute voice corpus counts for all avatars in one query
    avatar_ids = [a.id for a in avatars]
    corpus_counts = {}
    if avatar_ids:
        from sqlalchemy import func as sa_func
        count_result = await db.execute(
            select(VoiceCorpusEntry.avatar_id, sa_func.count(VoiceCorpusEntry.id))
            .where(VoiceCorpusEntry.avatar_id.in_(avatar_ids))
            .where(VoiceCorpusEntry.status == "ready")
            .group_by(VoiceCorpusEntry.avatar_id)
        )
        corpus_counts = dict(count_result.all())

    return AvatarListResponse(
        avatars=[_avatar_to_response(a, voice_corpus_count=corpus_counts.get(a.id, 0)) for a in avatars],
        total=len(avatars),
    )


@router.post("/clone-from-tiktok", response_model=AvatarResponse, status_code=201)
async def clone_from_tiktok(
    req: CloneFromTikTokRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not req.consent_confirmed:
        raise HTTPException(status_code=400, detail="Consent must be confirmed")

    # Normalize TikTok input: @handle, bare handle, partial URL → full URL
    tiktok_url = normalize_tiktok_input(req.tiktok_url)
    if not tiktok_url or "tiktok.com/@" not in tiktok_url:
        raise HTTPException(status_code=400, detail="Please enter a valid TikTok username or profile URL.")

    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=user.id,
        type=AvatarType.CLONE,
        status=AvatarStatus.PROCESSING,
        name=req.name or f"Clone from TikTok",
        tiktok_source_url=tiktok_url,
        test_script=req.test_script or None,
        progress_step="Queued for processing",
        progress_percent=0,
    )
    db.add(avatar)
    await db.commit()

    # DEPRECATED: Old clone flow. New flow uses /create + /process-segment.
    # Task launch removed (B-093) to prevent race conditions with new flow.
    logger.warning(f"Deprecated endpoint /clone-from-tiktok called for avatar {avatar_id}")

    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.create", entity_type="avatar",
            entity_id=avatar_id, after={"name": avatar.name, "type": "clone_tiktok"},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _avatar_to_response(avatar)


class CreateAvatarRequest(BaseModel):
    tiktok_url: Optional[str] = ""
    name: Optional[str] = ""


class CreateAvatarResponse(BaseModel):
    avatar_id: str


@router.post("/create", response_model=CreateAvatarResponse, status_code=201)
async def create_avatar(
    req: CreateAvatarRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an Avatar record ONLY — no Celery task launched.

    Used by the new clone flow (Sections A-G) so that processSegment can
    attach pipelines to an existing avatar without the old clone_avatar_task
    racing against the new image + voice pipelines (B-048 / B-060).
    """
    tiktok_url = normalize_tiktok_input(req.tiktok_url or "")
    # Only validate TikTok URL if one was provided (upload/record paths don't have one)
    if req.tiktok_url and req.tiktok_url.strip():
        if not tiktok_url or "tiktok.com/@" not in tiktok_url:
            raise HTTPException(status_code=400, detail="Please enter a valid TikTok username or profile URL.")

    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=user.id,
        type=AvatarType.CLONE,
        status=AvatarStatus.DRAFT,
        name=req.name or ("Clone from TikTok" if tiktok_url else "Clone Avatar"),
        tiktok_source_url=tiktok_url or None,
        progress_step="Draft",
        progress_percent=0,
        wizard_step="upload",
    )
    db.add(avatar)
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.create", entity_type="avatar",
            entity_id=avatar_id, after={"name": avatar.name, "type": "clone_draft"},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return CreateAvatarResponse(avatar_id=avatar_id)


@router.post("/clone-with-photo", response_model=AvatarResponse, status_code=201)
@router.post("/clone-with-media", response_model=AvatarResponse, status_code=201)
async def clone_with_media(
    name: str = Form(default=""),
    test_script: str = Form(default=""),
    tiktok_url: str = Form(default=""),
    consent_confirmed: bool = Form(default=False),
    photo: UploadFile | None = File(default=None),
    audio: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a clone avatar using uploaded photo and/or audio (camera, mic, or file upload)."""
    if not consent_confirmed:
        raise HTTPException(status_code=400, detail="Consent must be confirmed")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    face_key = None
    voice_sample_key = None

    # Upload photo if provided
    if photo and photo.filename:
        if photo.content_type not in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
            raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, or WebP image.")
        photo_bytes = await photo.read()
        if len(photo_bytes) < 1000:
            raise HTTPException(status_code=400, detail="Image file is too small.")
        if len(photo_bytes) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image file is too large. Maximum 10MB.")
        face_key = f"creators/{user.id}/avatar/{avatar_id}/face_ref.jpg"
        await r2.upload_bytes(photo_bytes, face_key, photo.content_type or "image/jpeg")

    # Upload audio if provided
    if audio and audio.filename:
        allowed_audio = ("audio/webm", "audio/mp4", "audio/mpeg", "audio/wav",
                         "audio/ogg", "audio/x-wav", "audio/webm;codecs=opus")
        # Be lenient with content-type matching
        if audio.content_type and not any(audio.content_type.startswith(a.split(";")[0]) for a in allowed_audio):
            raise HTTPException(status_code=400, detail=f"Unsupported audio format: {audio.content_type}")
        audio_bytes = await audio.read()
        if len(audio_bytes) < 500:
            raise HTTPException(status_code=400, detail="Audio file is too small.")
        if len(audio_bytes) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Audio file is too large. Maximum 20MB.")
        ext = "webm" if "webm" in (audio.content_type or "") else "mp3"
        voice_sample_key = f"creators/{user.id}/avatar/{avatar_id}/voice_sample.{ext}"
        await r2.upload_bytes(audio_bytes, voice_sample_key, audio.content_type or "audio/webm")

    # Normalize TikTok URL if provided
    normalized_tiktok = ""
    if tiktok_url:
        normalized_tiktok = normalize_tiktok_input(tiktok_url)

    # Need at least one input: photo, audio, or TikTok URL
    if not face_key and not voice_sample_key and not normalized_tiktok:
        raise HTTPException(status_code=400, detail="Please provide a photo, voice sample, or TikTok URL.")

    avatar = Avatar(
        id=avatar_id,
        user_id=user.id,
        type=AvatarType.CLONE,
        status=AvatarStatus.PROCESSING,
        name=name or "Clone Avatar",
        face_ref_key=face_key,
        voice_sample_key=voice_sample_key,
        tiktok_source_url=normalized_tiktok or None,
        test_script=test_script or None,
        progress_step="Queued for processing",
        progress_percent=0,
    )
    db.add(avatar)
    await db.commit()

    # DEPRECATED: Old clone flow. New flow uses /create + /process-segment.
    # Task launch removed (B-093) to prevent race conditions with new flow.
    logger.warning(f"Deprecated endpoint /clone-with-media called for avatar {avatar_id}")

    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.create", entity_type="avatar",
            entity_id=avatar_id, after={"name": avatar.name, "type": "clone_with_media"},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _avatar_to_response(avatar)


@router.post("/generate-digital", response_model=AvatarResponse, status_code=201)
async def generate_digital(
    req: GenerateDigitalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=user.id,
        type=AvatarType.DIGITAL,
        status=AvatarStatus.PROCESSING,
        name=req.name or "AI Character",
        description=req.description,
        voice_style=req.voice_style,
        background=req.background,
        camera_position=req.camera_position,
        style=req.style,
        ai_model=req.ai_model,
        progress_step="Queued for processing",
        progress_percent=0,
    )
    db.add(avatar)
    await db.commit()

    from tasks.generate_avatar import generate_digital_avatar_task
    generate_digital_avatar_task.delay(
        avatar_id, user.id,
        req.description or "", req.voice_style or "energetic",
        req.persona_preset or "energetic_beauty",
        req.background or "studio", req.camera_position or "waist_up",
        req.style or "photorealistic", req.ai_model or "meta-llama/llama-3-70b-instruct",
    )

    return _avatar_to_response(avatar)


@router.get("/status/{avatar_id}", response_model=AvatarResponse)
async def get_avatar_status(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from sqlalchemy import func as sa_func
    corpus_count = (await db.execute(
        select(sa_func.count(VoiceCorpusEntry.id))
        .where(VoiceCorpusEntry.avatar_id == avatar_id)
        .where(VoiceCorpusEntry.status == "ready")
    )).scalar() or 0

    resp = _avatar_to_response(avatar, voice_corpus_count=corpus_count)

    # Attach render_status from render_jobs
    render_status = await _get_render_status_for_avatar(db, avatar_id)
    if render_status:
        resp.render_status = render_status

    return resp


async def _get_render_status_for_avatar(db: AsyncSession, avatar_id: str) -> dict | None:
    """Get aggregated render status from render_jobs for an avatar."""
    from sqlalchemy import text as sa_text
    row = await db.execute(
        sa_text("""
            SELECT state, error_message, progress_percent, job_type,
                   EXTRACT(EPOCH FROM (NOW() - queued_at)) AS elapsed_seconds
            FROM render_jobs
            WHERE avatar_id = :avatar_id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"avatar_id": avatar_id},
    )
    result = row.fetchone()
    if not result:
        return None

    from services.render_eta import estimate_wait_seconds
    eta = await estimate_wait_seconds(db, result.job_type, "runpod_infinitetalk")

    return {
        "state": result.state,
        "eta": eta.get("estimated_seconds"),
        "position": eta.get("position", 0),
        "confidence": eta.get("confidence", "none"),
        "error_message": result.error_message,
        "progress_percent": result.progress_percent,
        "elapsed_seconds": int(result.elapsed_seconds) if result.elapsed_seconds else None,
    }


@router.post("/{avatar_id}/approve", response_model=AvatarResponse)
async def approve_avatar(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve the avatar after reviewing the test video. Makes it available for Casts."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.status != AvatarStatus.READY:
        raise HTTPException(status_code=400, detail="Avatar must be in 'ready' status to approve")

    avatar.status = AvatarStatus.APPROVED

    # Auto-create "Original" AvatarLook if none exists yet
    if avatar.face_ref_key:
        existing = await db.execute(
            select(AvatarLook)
            .where(AvatarLook.avatar_id == avatar_id, AvatarLook.is_original == True)
        )
        if not existing.scalars().first():
            original_look = AvatarLook(
                id=f"al_{uuid.uuid4().hex[:12]}",
                avatar_id=avatar_id,
                name="Original",
                face_ref_key=avatar.face_ref_key,
                is_default=True,
                is_original=True,
                status="ready",
                look_type="background",
            )
            db.add(original_look)

    # Seed body_motion AvatarLook rows from the most recent completed BodyShotSet
    # so the cast builder's body-motion picker is populated with the angle photos
    # the user already approved on the onboarding screen (no re-render needed).
    seeded_count = 0
    try:
        BODY_MOTION_POSES = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"]
        POSE_LABELS = {
            "front": "Front",
            "three_quarter_left": "3/4 Left",
            "three_quarter_right": "3/4 Right",
            "profile_left": "Profile Left",
            "profile_right": "Profile Right",
            "back": "Back",
        }
        bss_result = await db.execute(
            select(BodyShotSet)
            .where(BodyShotSet.avatar_id == avatar_id, BodyShotSet.status == "completed")
            .order_by(BodyShotSet.created_at.desc())
            .limit(1)
        )
        body_shot_set = bss_result.scalars().first()
        if body_shot_set and body_shot_set.angles:
            angles = body_shot_set.angles
            for pose in BODY_MOTION_POSES:
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
                label = POSE_LABELS[pose]
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

    await db.commit()
    await db.refresh(avatar)
    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.approve", entity_type="avatar",
            entity_id=avatar_id, after={"status": str(avatar.status)},
        )
        if seeded_count > 0:
            await audit_log.record(
                db, user_id=user.id, action="avatar.body_motion_seeded", entity_type="avatar",
                entity_id=avatar_id, after={"count": seeded_count},
            )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _avatar_to_response(avatar)


@router.post("/{avatar_id}/regenerate", response_model=AvatarResponse)
async def regenerate_avatar(
    avatar_id: str,
    req: RegenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate the avatar's test video with optional new test script."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.status not in (AvatarStatus.READY, AvatarStatus.APPROVED, AvatarStatus.FAILED, AvatarStatus.FACE_CANDIDATES_READY, AvatarStatus.PROCESSING):
        raise HTTPException(status_code=400, detail="Cannot regenerate avatar in current status")

    # B-099: Enforce free regeneration limit
    FREE_REGENERATIONS = 2
    if (avatar.regeneration_count or 0) >= FREE_REGENERATIONS:
        raise HTTPException(
            status_code=402,
            detail=f"Free regenerations used ({avatar.regeneration_count}/{FREE_REGENERATIONS}). Additional regenerations cost $1.99."
        )

    # Update test script if provided
    if req.test_script:
        avatar.test_script = req.test_script

    # Reset to processing
    avatar.status = AvatarStatus.PROCESSING
    avatar.test_video_key = None
    avatar.test_audio_key = None
    avatar.progress_step = "Regenerating with new script..."
    avatar.progress_percent = 50  # Skip face generation, go straight to audio+video
    avatar.regeneration_count = (avatar.regeneration_count or 0) + 1
    await db.commit()

    # Launch regeneration task (only audio + video steps)
    from tasks.generate_avatar import regenerate_avatar_video_task
    regenerate_avatar_video_task.delay(
        avatar_id, user.id,
        req.test_script or avatar.test_script or "",
    )

    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.regenerate", entity_type="avatar",
            entity_id=avatar_id, after={"regeneration_count": avatar.regeneration_count},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _avatar_to_response(avatar)


@router.post("/{avatar_id}/reclone-voice")
async def reclone_voice(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run only the voice cloning pipeline for an avatar.

    Used when the initial voice clone produced a bad result (e.g. GPU was busy,
    BS-RoFormer fell back to CPU and timed out, resulting in wrong gender voice).
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if avatar.status not in (AvatarStatus.READY, AvatarStatus.FACE_CANDIDATES_READY, AvatarStatus.APPROVED):
        raise HTTPException(status_code=400, detail="Avatar must be in ready or approved status to re-clone voice")

    if not avatar.video_ref_key and not avatar.voice_sample_key:
        raise HTTPException(status_code=400, detail="No voice reference found for this avatar. Cannot re-clone voice.")

    if avatar.video_ref_key:
        from tasks.generate_avatar import process_voice_pipeline_task
        process_voice_pipeline_task.delay(avatar_id, user.id, avatar.video_ref_key, segment_start, segment_end)
    else:
        # audio-sample path — reuse the same clone helper used by /clone-voice
        r2 = get_r2_storage_service()
        fish = get_fish_audio_service()

        voice_url = r2.get_public_url(avatar.voice_sample_key)
        voice_id = await fish.clone_voice(voice_url, name=f"Re-clone for {avatar.name or avatar.id}")
        avatar.voice_id = voice_id
        avatar.voice_clone_progress = 100
        avatar.progress_step = "Voice re-cloned"
        await db.commit()
    # Get segment info from persona_profile
    segment_start = (avatar.persona_profile or {}).get("segment_start", 0)
    segment_end = (avatar.persona_profile or {}).get("segment_end", 60)

    avatar.voice_clone_progress = 0
    avatar.progress_step = "Re-cloning voice..."
    await db.commit()

    from tasks.generate_avatar import process_voice_pipeline_task
    process_voice_pipeline_task.delay(
        avatar_id, user.id,
        avatar.video_ref_key, segment_start, segment_end,
    )

    return {"status": "ok", "message": "Voice re-clone started"}


@router.get("/{avatar_id}/video")
async def stream_avatar_video(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream the avatar test video directly. Supports Range requests for smooth playback."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if not avatar.test_video_key:
        raise HTTPException(status_code=404, detail="No test video available")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    try:
        obj = r2.client.get_object(Bucket=r2.bucket, Key=avatar.test_video_key)
        content_length = obj["ContentLength"]

        return StreamingResponse(
            obj["Body"].iter_chunks(chunk_size=65536),
            media_type="video/mp4",
            headers={
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": f'inline; filename="{avatar_id}_preview.mp4"',
            },
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Video stream error: {str(e)}")


@router.get("/{avatar_id}/candidates", response_model=CandidatesResponse)
async def get_candidates(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return candidate frame URLs for an avatar in candidates_ready status."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.status != AvatarStatus.CANDIDATES_READY:
        raise HTTPException(status_code=400, detail="Avatar does not have candidate frames ready")
    # Convert R2 keys to CDN URLs at response time (B-068)
    frames = [_r2_key_to_url(f) for f in (avatar.candidate_frames or [])]
    return CandidatesResponse(
        avatar_id=avatar.id,
        status=avatar.status.value,
        candidate_frames=frames,
    )


@router.post("/{avatar_id}/select-frame", response_model=AvatarResponse)
async def select_frame(
    avatar_id: str,
    req: SelectFrameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Select a candidate frame URL and launch the remaining pipeline."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.status != AvatarStatus.CANDIDATES_READY:
        raise HTTPException(status_code=400, detail="Avatar is not awaiting frame selection")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    public_base = r2.get_public_url("").rstrip("/")
    if req.frame_url.startswith(public_base):
        face_ref_key = req.frame_url[len(public_base):].lstrip("/")
    else:
        face_ref_key = req.frame_url

    if face_ref_key and not any(face_ref_key.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
        face_ref_key = f"{face_ref_key.rstrip('/')}/face_ref.jpg"

    avatar.face_ref_key = face_ref_key
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = "Frame selected — continuing pipeline..."
    avatar.progress_percent = 40
    await db.commit()

    from tasks.generate_avatar import generate_from_selection_task
    generate_from_selection_task.delay(avatar_id, user.id)
    return _avatar_to_response(avatar)


@router.post("/{avatar_id}/capture-frame", response_model=AvatarResponse)
async def capture_frame(
    avatar_id: str,
    frame: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a captured frame from the video scrubber and launch the remaining pipeline."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.status != AvatarStatus.CANDIDATES_READY:
        raise HTTPException(status_code=400, detail="Avatar is not awaiting frame capture")

    frame_bytes = await frame.read()
    if len(frame_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Captured frame is too small.")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    face_key = f"creators/{user.id}/avatar/{avatar_id}/face_ref.jpg"
    await r2.upload_bytes(frame_bytes, face_key, "image/jpeg")

    avatar.face_ref_key = face_key
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = "Frame captured — cloning voice and generating video..."
    avatar.progress_percent = 40
    await db.commit()

    from tasks.generate_avatar import generate_from_selection_task
    generate_from_selection_task.delay(avatar_id, user.id)
    return _avatar_to_response(avatar)


# ═══════════════════════════════════════════════════════════════════════
# NEW CLONE FLOW ENDPOINTS — clone-flow-rebuild
# ═══════════════════════════════════════════════════════════════════════

@router.post("/fetch-videos", response_model=FetchVideosResponse)
async def fetch_videos(
    req: FetchVideosRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch TikTok video metadata with server-side caching and pagination.

    First request fetches ALL videos from Apify, runs face detection on all,
    and caches the full result in scraping_jobs (24h TTL). Subsequent pages
    are served from cache.
    """
    import httpx
    import asyncio
    import hashlib
    import math
    from datetime import datetime, timedelta
    from models.scraping_job import ScrapingJob

    tiktok_url = normalize_tiktok_input(req.tiktok_url)
    if not tiktok_url or "tiktok.com/@" not in tiktok_url:
        raise HTTPException(status_code=400, detail="Please enter a valid TikTok username or profile URL.")

    # Extract handle for cache lookup
    import re as _re
    handle_match = _re.search(r'tiktok\.com/@([^/?&#]+)', tiktok_url)
    tiktok_handle = handle_match.group(1).lower() if handle_match else tiktok_url

    logger.info("fetch_videos called", extra={"handle": tiktok_handle, "page": req.page, "per_page": req.per_page})

    # ── Check cache ──
    now = datetime.utcnow()
    cached = (await db.execute(
        select(ScrapingJob)
        .where(ScrapingJob.handle == tiktok_handle)
        .where(ScrapingJob.platform == "tiktok")
        .where(ScrapingJob.expires_at > now)
        .order_by(ScrapingJob.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    cache_entry = None

    if cached and cached.result_data:
        all_videos = cached.result_data
        cache_entry = cached
        logger.info("Cache hit", extra={"handle": tiktok_handle, "total": len(all_videos)})
    else:
        # ── Fetch from Apify (metadata only — no face detection yet) ──
        from services.apify_tiktok import get_apify_tiktok_service
        apify = get_apify_tiktok_service()
        videos = await apify.fetch_tiktok_videos(tiktok_url, max_videos=100)

        if not videos:
            raise HTTPException(status_code=404, detail="No videos found. The account may be private or empty.")

        logger.info("Apify returned videos", extra={"count": len(videos), "handle": tiktok_handle})

        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        handle_hash = hashlib.md5(tiktok_url.encode()).hexdigest()[:8]

        async def proxy_cover_only(i: int, cover_url: str) -> str:
            """Proxy cover image to R2, returns CDN URL. No face detection."""
            if not cover_url:
                return ""
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(cover_url)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        cover_hash = hashlib.md5(cover_url.encode()).hexdigest()[:8]
                        key = f"covers/{handle_hash}/{cover_hash}_{i}.jpg"
                        await r2.upload_bytes(
                            resp.content, key, "image/jpeg",
                            cache_control="no-cache, no-store, must-revalidate",
                        )
                        return r2.get_public_url(key, cache_bust=True)
            except Exception:
                pass
            return ""

        # Proxy all cover images to R2 (fast, no face detection)
        cover_tasks = [
            proxy_cover_only(i, v.get("cover_url", ""))
            for i, v in enumerate(videos)
        ]
        cover_results = await asyncio.gather(*cover_tasks)

        all_videos = []
        for i, v in enumerate(videos):
            web_url = v.get("video_url", "") or v.get("video_download_url", "")
            duration = float(v.get("duration", 0) or 0)
            stats = v.get("stats", {})
            thumb_url = cover_results[i] if i < len(cover_results) else ""

            if not thumb_url and not web_url:
                continue

            create_time = v.get("createTimeISO", "") or v.get("createTime", "")
            likes = stats.get("diggCount", 0) if isinstance(stats, dict) else 0
            comments_data = None
            raw_comments = v.get("comments", None)
            if raw_comments and isinstance(raw_comments, list):
                comments_data = []
                for c in raw_comments[:5]:
                    comments_data.append({
                        "user": c.get("uniqueId", c.get("user", {}).get("uniqueId", "unknown")),
                        "text": (c.get("text", "") or "")[:200],
                        "likes": c.get("diggCount", 0),
                    })

            all_videos.append({
                "thumb_url": thumb_url,
                "video_url": "",
                "web_video_url": web_url,
                "duration_seconds": duration,
                "video_r2_key": "",
                "description": (v.get("description", "") or "")[:200],
                "views": stats.get("playCount", stats.get("views", 0)) if isinstance(stats, dict) else 0,
                "has_face": None,  # Not yet detected — lazy
                "face_confidence": 0.0,
                "likes": likes,
                "create_time": str(create_time) if create_time else None,
                "comments": comments_data,
            })

        if not all_videos:
            raise HTTPException(status_code=500, detail="Could not fetch any TikTok video metadata.")

        # ── Store in cache (no face detection yet) ──
        job_id = f"scrape_{uuid.uuid4().hex[:12]}"
        cache_entry = ScrapingJob(
            id=job_id,
            user_id=user.id,
            platform="tiktok",
            handle=tiktok_handle,
            normalized_url=tiktok_url,
            status="completed",
            video_count=len(all_videos),
            result_data=all_videos,
            expires_at=now + timedelta(hours=24),
        )
        db.add(cache_entry)
        await db.commit()
        logger.info("Cached videos (no face detection yet)", extra={"handle": tiktok_handle, "count": len(all_videos)})

    # ── Paginate ──
    total = len(all_videos)
    total_pages = math.ceil(total / req.per_page)
    start = (req.page - 1) * req.per_page
    end = start + req.per_page
    page_slice = all_videos[start:end]

    # ── Lazy face detection: only run on this page's videos if not yet done ──
    needs_face_detection = any(v.get("has_face") is None for v in page_slice)
    if needs_face_detection:
        from services.r2_storage import get_r2_storage_service
        from services.face_extraction import detect_face_in_image
        r2 = get_r2_storage_service()

        async def detect_face_for_video(video: dict) -> dict:
            """Run face detection on a single video's cover image."""
            if video.get("has_face") is not None:
                return video  # Already processed
            thumb_url = video.get("thumb_url", "")
            if not thumb_url:
                video["has_face"] = False
                video["face_confidence"] = 0.0
                return video
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(thumb_url)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        import asyncio as _asyncio
                        loop = _asyncio.get_event_loop()
                        has_face, confidence = await loop.run_in_executor(
                            None, detect_face_in_image, resp.content
                        )
                        video["has_face"] = has_face
                        video["face_confidence"] = confidence
                        return video
            except Exception:
                pass
            video["has_face"] = False
            video["face_confidence"] = 0.0
            return video

        face_tasks = [detect_face_for_video(v) for v in page_slice]
        page_slice = await asyncio.gather(*face_tasks)
        logger.info("Lazy face detection completed for page", extra={
            "handle": tiktok_handle, "page": req.page,
            "detected": sum(1 for v in page_slice if v.get("has_face")),
        })

        # Update cache with face detection results for this page
        if cache_entry:
            for i, v in enumerate(page_slice):
                idx = start + i
                if idx < len(all_videos):
                    all_videos[idx]["has_face"] = v["has_face"]
                    all_videos[idx]["face_confidence"] = v["face_confidence"]
            cache_entry.result_data = all_videos
            await db.commit()

    page_videos = [FetchedVideo(**v) for v in page_slice]

    logger.info("fetch_videos returning", extra={
        "handle": tiktok_handle, "page": req.page, "total": total, "count": len(page_videos),
    })
    return FetchVideosResponse(
        videos=page_videos,
        total=total,
        page=req.page,
        per_page=req.per_page,
        total_pages=total_pages,
    )


class DownloadVideoRequest(BaseModel):
    web_video_url: str

class DownloadVideoResponse(BaseModel):
    video_url: str
    video_r2_key: str
    duration_seconds: float

@router.post("/download-video", response_model=DownloadVideoResponse)
async def download_video(
    req: DownloadVideoRequest,
    user: User = Depends(get_current_user),
):
    """Download a single TikTok video via yt-dlp, faststart it, upload to R2.
    Called when user selects a video from the gallery.
    """
    import subprocess
    import tempfile
    import os
    import hashlib

    video_hash = hashlib.md5(req.web_video_url.encode()).hexdigest()[:8]
    tmp_video = os.path.join(tempfile.gettempdir(), f"dl_{user.id}_{video_hash}.mp4")
    tmp_fast = os.path.join(tempfile.gettempdir(), f"dl_{user.id}_{video_hash}_fast.mp4")

    try:
        # Download via yt-dlp
        result = subprocess.run(
            ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
             "--no-playlist", "--max-filesize", "50M",
             "-o", tmp_video, "--no-warnings", "--quiet", req.web_video_url],
            capture_output=True, timeout=90,
        )
        if result.returncode != 0 or not os.path.exists(tmp_video):
            raise HTTPException(status_code=500, detail="Failed to download video from TikTok.")

        # Get duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp_video],
            capture_output=True, timeout=10,
        )
        duration = 0.0
        if probe.returncode == 0:
            try:
                duration = float(probe.stdout.decode().strip())
            except (ValueError, AttributeError):
                pass

        # Faststart for streaming
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_video, "-c", "copy",
             "-movflags", "+faststart", tmp_fast],
            capture_output=True, timeout=30,
        )
        upload_path = tmp_fast if os.path.exists(tmp_fast) else tmp_video

        # Upload to R2
        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        video_r2_key = f"creators/{user.id}/videos/{video_hash}.mp4"
        await r2.upload_file(upload_path, video_r2_key, content_type="video/mp4")
        video_cdn_url = r2.get_public_url(video_r2_key)

        return DownloadVideoResponse(
            video_url=video_cdn_url,
            video_r2_key=video_r2_key,
            duration_seconds=round(duration, 1),
        )
    finally:
        for p in [tmp_video, tmp_fast]:
            try:
                os.unlink(p)
            except OSError:
                pass


@router.post("/{avatar_id}/process-segment", response_model=AvatarResponse)
async def process_segment(
    avatar_id: str,
    req: ProcessSegmentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Receive a video segment selection and kick off image + voice pipelines in parallel.

    Image pipeline: extract frames → MediaPipe → Gemini scoring → face_candidates_ready
    Voice pipeline: extract audio → BS-RoFormer → normalize → Fish Audio clone
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="end_seconds must be greater than start_seconds")

    if req.end_seconds - req.start_seconds < 15:
        raise HTTPException(status_code=400, detail="Segment must be at least 15 seconds")

    # Update avatar status and save segment info for potential re-clone
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = "Processing video segment..."
    avatar.progress_percent = 20
    avatar.voice_clone_progress = 0
    avatar.video_ref_key = req.video_r2_key
    # Store segment times in persona_profile for re-clone capability
    segment_info = {"segment_start": req.start_seconds, "segment_end": req.end_seconds}
    avatar.persona_profile = {**(avatar.persona_profile or {}), **segment_info}
    await db.commit()

    # Kick off BOTH pipelines in parallel via Celery
    from tasks.generate_avatar import process_image_pipeline_task, process_voice_pipeline_task

    process_image_pipeline_task.delay(
        avatar_id, user.id,
        req.video_r2_key, req.start_seconds, req.end_seconds,
    )
    process_voice_pipeline_task.delay(
        avatar_id, user.id,
        req.video_r2_key, req.start_seconds, req.end_seconds,
    )

    await db.refresh(avatar)
    return _avatar_to_response(avatar)


@router.post("/{avatar_id}/edit-frame", response_model=EditFrameResponse)
async def edit_frame(
    avatar_id: str,
    req: EditFrameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a face candidate frame using FLUX Kontext Pro.

    Removes TikTok captions/watermarks/UI elements, preserves identity,
    and applies optional user instructions. Downloads result and re-uploads
    to R2 as the avatar's face_ref.jpg.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.flux_kontext import edit_avatar_frame
    from services.r2_storage import get_r2_storage_service
    import httpx

    r2 = get_r2_storage_service()

    try:
        edited_url = await edit_avatar_frame(req.frame_url, req.instructions)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Face editing failed: {str(e)[:200]}")

    # Download the edited image and re-upload to R2 with unique key (bust CDN cache)
    try:
        import time as _time
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(edited_url)
            resp.raise_for_status()
            edited_bytes = resp.content

        ts = int(_time.time())
        face_key = f"creators/{user.id}/avatar/{avatar_id}/face_ref_{ts}.jpg"
        await r2.upload_bytes(edited_bytes, face_key, "image/jpeg", cache_control="no-cache, no-store, must-revalidate")

        avatar.face_ref_key = face_key

        # Phase ownership: advance from IMAGE to VOICE or RENDER
        from models.avatar import AvatarPhase
        voice_done = (avatar.voice_clone_progress or 0) >= 100 and avatar.voice_id

        if voice_done:
            # Skip straight to render phase -- voice was faster than user
            avatar.active_phase = AvatarPhase.RENDER
            avatar.status = AvatarStatus.PROCESSING
            avatar.progress_step = "Generating test video..."
            avatar.progress_percent = 80
            await db.commit()
            from tasks.generate_avatar import regenerate_avatar_video_task
            regenerate_avatar_video_task.delay(avatar_id, user.id, avatar.test_script or "")
        else:
            # Voice still running -- advance to voice phase so its progress messages flow through
            avatar.active_phase = AvatarPhase.VOICE
            avatar.progress_step = "Cloning your voice..."
            avatar.progress_percent = 50
            await db.commit()

        return EditFrameResponse(
            original_url=req.frame_url,
            edited_url=r2.get_public_url(face_key, cache_bust=True),
        )
    except httpx.HTTPError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Failed to download edited image: {str(e)[:200]}")


@router.get("/{avatar_id}/face-candidates", response_model=FaceCandidatesResponse)
async def get_face_candidates(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return scored face candidate URLs for this avatar.

    Supports polling: returns empty candidates while PROCESSING, populated
    candidates once FACE_CANDIDATES_READY or CANDIDATES_READY. Also reports
    voice_clone_progress so frontend can track both pipelines.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    allowed = (
        AvatarStatus.PROCESSING,
        AvatarStatus.FACE_CANDIDATES_READY,
        AvatarStatus.CANDIDATES_READY,
        AvatarStatus.READY,
        AvatarStatus.APPROVED,
        AvatarStatus.FAILED,  # Allow polling to detect failure
    )
    if avatar.status not in allowed:
        raise HTTPException(status_code=400, detail="Avatar does not have face candidates ready")

    frames = avatar.candidate_frames or []
    scores = avatar.candidate_scores or []
    # Use real scores from pipeline if available; fall back to synthetic scores for legacy avatars
    # Convert R2 keys to CDN URLs at response time (B-068)
    candidates = []
    for i, key in enumerate(frames):
        url = _r2_key_to_url(key)
        if i < len(scores) and scores[i]:
            score = round(float(scores[i]), 2)
        else:
            score = round(1.0 - i * (0.8 / max(len(frames) - 1, 1)), 2)
        candidates.append(FaceCandidateItem(url=url, score=score))

    return FaceCandidatesResponse(
        avatar_id=avatar.id,
        status=avatar.status.value,
        candidates=candidates,
        voice_clone_progress=avatar.voice_clone_progress,
        progress_step=avatar.progress_step or "",
    )


class UploadFaceResponse(BaseModel):
    face_ref_key: str
    face_url: str


@router.post("/{avatar_id}/upload-face", response_model=UploadFaceResponse)
async def upload_face(
    avatar_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manually captured frame as the face reference, bypassing auto face extraction.

    Accepts a JPEG/PNG image, uploads to R2, sets face_ref_key, and advances
    status to FACE_CANDIDATES_READY so the rest of the flow continues.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if file.content_type not in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
        raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, or WebP image.")

    file_bytes = await file.read()
    if len(file_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Image file is too small.")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file is too large. Maximum 10MB.")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    face_key = f"creators/{user.id}/avatar/{avatar_id}/face_ref_manual.jpg"
    await r2.upload_bytes(file_bytes, face_key, file.content_type or "image/jpeg")

    avatar.face_ref_key = face_key
    avatar.status = AvatarStatus.FACE_CANDIDATES_READY
    await db.commit()

    return UploadFaceResponse(
        face_ref_key=face_key,
        face_url=r2.get_public_url(face_key),
    )


class UploadFrameResponse(BaseModel):
    frame_url: str
    r2_key: str


@router.post("/{avatar_id}/upload-frame", response_model=UploadFrameResponse)
async def upload_frame(
    avatar_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manually captured video frame as a face candidate.

    Stores in R2 at creators/{user_id}/avatar/{avatar_id}/manual_frame_{timestamp}.jpg
    and adds to the candidate_frames array on the avatar.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if file.content_type not in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
        raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, or WebP image.")

    file_bytes = await file.read()
    if len(file_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Image file is too small.")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file is too large. Maximum 10MB.")

    import time
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    timestamp = int(time.time() * 1000)
    r2_key = f"creators/{user.id}/avatar/{avatar_id}/manual_frame_{timestamp}.jpg"
    await r2.upload_bytes(file_bytes, r2_key, file.content_type or "image/jpeg")

    # Add to candidate_frames array
    frames = list(avatar.candidate_frames or [])
    frames.insert(0, r2_key)  # user frames first
    avatar.candidate_frames = frames
    await db.commit()

    return UploadFrameResponse(
        frame_url=r2.get_public_url(r2_key),
        r2_key=r2_key,
    )


class SelectFaceRequest(BaseModel):
    face_url: str


@router.post("/{avatar_id}/select-face")
async def select_face(
    avatar_id: str,
    req: SelectFaceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persist the user's chosen face candidate URL as face_ref_key on the Avatar.

    Called when user clicks "Continue with this face" or "Use original" without
    going through the FLUX Kontext edit-frame endpoint (B-054).
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    public_base = r2.get_public_url("").rstrip("/")
    if req.face_url.startswith(public_base):
        face_ref_key = req.face_url[len(public_base):].lstrip("/")
    else:
        face_ref_key = req.face_url

    if face_ref_key and not any(face_ref_key.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
        face_ref_key = f"{face_ref_key.rstrip('/')}/face_ref.jpg"

    avatar.face_ref_key = face_ref_key

    # Phase ownership: advance from IMAGE to VOICE or RENDER
    from models.avatar import AvatarPhase
    voice_done = (avatar.voice_clone_progress or 0) >= 100 and avatar.voice_id

    if voice_done:
        # Skip straight to render phase -- voice was faster than user
        avatar.active_phase = AvatarPhase.RENDER
        avatar.status = AvatarStatus.PROCESSING
        avatar.progress_step = "Generating test video..."
        avatar.progress_percent = 80
        await db.commit()
        from tasks.generate_avatar import regenerate_avatar_video_task
        test_script = avatar.test_script or ""
        regenerate_avatar_video_task.delay(avatar_id, user.id, test_script)
    else:
        # Voice still running -- advance to voice phase so its progress messages flow through
        avatar.active_phase = AvatarPhase.VOICE
        avatar.progress_step = "Cloning your voice..."
        avatar.progress_percent = 50
        await db.commit()

    return {"status": "ok"}


class UploadVideoResponse(BaseModel):
    video_r2_key: str
    video_url: str
    duration_seconds: float


@router.post("/upload-video", response_model=UploadVideoResponse)
async def upload_video(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload a video file for avatar cloning. Validates format, size, duration.
    Uploads to R2 and returns the key + CDN URL + duration.
    """
    import subprocess
    import tempfile
    import os
    import json as _json

    # Validate extension
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("mp4", "mov", "webm"):
        raise HTTPException(status_code=400, detail="Unsupported format. Please upload .mp4, .mov, or .webm")

    # Read file and validate size (200MB max)
    contents = await file.read()
    if len(contents) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum 200MB.")
    if len(contents) < 10000:
        raise HTTPException(status_code=400, detail="File too small or empty.")

    # Save to temp file for ffprobe
    tmp_path = os.path.join(tempfile.gettempdir(), f"upload_{user.id}_{uuid.uuid4().hex[:8]}.{ext}")
    try:
        with open(tmp_path, "wb") as f:
            f.write(contents)

        # ffprobe duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
            capture_output=True, timeout=15,
        )
        duration = 0.0
        if probe.returncode == 0:
            try:
                duration = float(probe.stdout.decode().strip())
            except (ValueError, AttributeError):
                pass

        if duration < 15:
            raise HTTPException(status_code=400, detail="Video too short. Minimum 15 seconds.")
        if duration > 180:
            raise HTTPException(status_code=400, detail="Video too long. Maximum 3 minutes.")

        # Upload to R2
        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        video_r2_key = f"creators/{user.id}/uploads/{uuid.uuid4().hex[:12]}.{ext}"
        content_type = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}.get(ext, "video/mp4")
        await r2.upload_bytes(contents, video_r2_key, content_type)
        video_url = r2.get_public_url(video_r2_key)

        return UploadVideoResponse(
            video_r2_key=video_r2_key,
            video_url=video_url,
            duration_seconds=round(duration, 1),
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass



@router.put("/{avatar_id}")
@router.patch("/{avatar_id}")
async def update_avatar(
    avatar_id: str,
    update: dict = Body(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")
    # Whitelist of updatable fields
    allowed = {
        "name", "body_description", "description", "target_audience",
        "gender", "style_preset", "wizard_step",
        # PR #65: clip-on lavalier vs phone-mic toggle. Drives the TTS
        # post-process EQ profile AND the mic-style suffix on the voice
        # description used by the cloning engine.
        "clip_mic_enabled",
    }
    changed: dict = {}
    for key, value in update.items():
        if key not in allowed:
            continue
        if value is None and key != "clip_mic_enabled":
            continue
        if key == "clip_mic_enabled":
            # Coerce truthy/falsy ints / strings into a strict bool.
            setattr(avatar, key, bool(value))
            changed[key] = bool(value)
            continue
        setattr(avatar, key, str(value)[:2000] if isinstance(value, str) else value)
        changed[key] = value if not isinstance(value, str) else value[:200]
    await db.commit()
    await db.refresh(avatar)
    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.update", entity_type="avatar",
            entity_id=avatar_id, after=changed,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _avatar_to_response(avatar)


# ═══════════════════════════════════════════════════════════════════════
# Avatar Style DNA — clone editing style from 1-3 reference creator videos
# ═══════════════════════════════════════════════════════════════════════
#
# The pipeline:
#   1. Download each video via yt-dlp (~$0.05/video Apify-equivalent for
#      tracking; yt-dlp itself is free, but we still log the event)
#   2. Extract a vocal-only WAV via ffmpeg + BS-RoFormer (HOSTKEY)
#   3. Concatenate the per-video vocal clips, transcribe via Whisper
#      (HOSTKEY)
#   4. Clone the voice via Fish Speech (HOSTKEY) — overwrites any existing
#      voice clone tied to this avatar
#   5. Run a Claude-Sonnet pass on the combined transcript to extract
#      pacing, b-roll ratio, caption preset, common phrases, etc.
#
# Per-video failures are tolerated: if at least one of the URLs makes it
# through steps 1-3, we proceed with what we have. If all of them fail
# the endpoint returns a 500 with a friendly message.

class StyleDNARequest(BaseModel):
    urls: list[str]


def _strip_style_dna_json_fences(raw: str) -> str:
    """Pull a JSON object out of a possibly-fenced LLM reply."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
        cleaned = cleaned.strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
    return cleaned


async def _download_video_for_style(url: str, user_id: str) -> tuple[str, float]:
    """Download a single creator video to a temp mp4. Returns (path, duration_s)."""
    import hashlib
    import os
    import subprocess
    import tempfile

    video_hash = hashlib.md5(url.encode()).hexdigest()[:10]
    tmp_video = os.path.join(tempfile.gettempdir(), f"styledna_{user_id}_{video_hash}.mp4")
    result = subprocess.run(
        [
            "yt-dlp", "-f", "mp4/best[ext=mp4]/best",
            "--no-playlist", "--max-filesize", "60M",
            "-o", tmp_video, "--no-warnings", "--quiet", url,
        ],
        capture_output=True, timeout=90,
    )
    if result.returncode != 0 or not os.path.exists(tmp_video):
        raise RuntimeError(f"yt-dlp failed for {url}")

    probe = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", tmp_video,
        ],
        capture_output=True, timeout=10,
    )
    duration = 0.0
    if probe.returncode == 0:
        try:
            duration = float(probe.stdout.decode().strip())
        except (ValueError, AttributeError):
            duration = 0.0
    return tmp_video, duration


async def _extract_audio_wav(video_path: str) -> str:
    """ffmpeg → mono 16k WAV. Returns local path."""
    import os
    import subprocess
    import tempfile

    audio_path = os.path.join(
        tempfile.gettempdir(), f"styledna_audio_{os.path.basename(video_path)}.wav",
    )
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            audio_path,
        ],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0 or not os.path.exists(audio_path):
        raise RuntimeError(f"ffmpeg audio extract failed for {video_path}")
    return audio_path


async def _concat_audio_files(paths: list[str]) -> tuple[str, float]:
    """Concatenate WAV files via ffmpeg concat demuxer. Returns (path, duration_s)."""
    import os
    import subprocess
    import tempfile

    if len(paths) == 1:
        # Still probe duration for consistency
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", paths[0],
            ],
            capture_output=True, timeout=10,
        )
        try:
            return paths[0], float(probe.stdout.decode().strip())
        except (ValueError, AttributeError):
            return paths[0], 0.0

    list_path = os.path.join(tempfile.gettempdir(), f"concat_{uuid.uuid4().hex[:8]}.txt")
    with open(list_path, "w") as fh:
        for p in paths:
            fh.write(f"file '{p}'\n")
    out_path = os.path.join(tempfile.gettempdir(), f"styledna_combined_{uuid.uuid4().hex[:8]}.wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", out_path],
        capture_output=True, timeout=120,
    )
    try:
        os.unlink(list_path)
    except OSError:
        pass
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg concat failed")

    probe = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", out_path,
        ],
        capture_output=True, timeout=10,
    )
    duration = 0.0
    try:
        duration = float(probe.stdout.decode().strip())
    except (ValueError, AttributeError):
        duration = 0.0
    return out_path, duration


@router.post("/{avatar_id}/analyze-style")
async def analyze_style_dna(
    avatar_id: str,
    req: StyleDNARequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Analyze 1-3 creator videos to extract Style DNA for an avatar.

    Returns the persisted Style DNA dict. Voice clone id is also written to
    `avatar.voice_id` so the existing TTS path picks it up automatically.
    """
    import json
    import os

    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    urls = [u.strip() for u in (req.urls or []) if u and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="At least one video URL is required")
    urls = urls[:3]

    from services.r2_storage import get_r2_storage_service
    from services.gpu_server import get_gpu_server_client
    from services.fish_audio import get_fish_audio_service
    from services.openrouter import get_openrouter_service
    from services.usage_tracker import log_usage, calculate_llm_cost

    r2 = get_r2_storage_service()
    gpu_client = get_gpu_server_client()

    # ── Steps 1-4 per video, independent. Drop failures, keep survivors. ──
    survivors: list[dict] = []  # {url, audio_path, transcript, duration_s}
    tmp_files: list[str] = []

    for url in urls:
        try:
            # 1. download
            video_path, _video_dur = await _download_video_for_style(url, user.id)
            tmp_files.append(video_path)
            await log_usage(
                db, user_id=user.id, event_type="video_scrape",
                provider="apify", provider_cost_usd=0.05,
                quantity=1, quantity_unit="videos",
                resource_type="avatar", resource_id=avatar.id,
            )

            # 2. extract audio
            raw_audio = await _extract_audio_wav(video_path)
            tmp_files.append(raw_audio)

            # 3. (optional) BS-RoFormer voice separation. If the GPU server
            #    is unreachable or returns an error we degrade to the raw
            #    audio — voice cloning still works, just on noisier input.
            #    TODO: tighten the fallback once BS-RoFormer is more stable
            #    across the deployed fleet.
            clean_audio = raw_audio
            if gpu_client is not None:
                try:
                    audio_key = f"creators/{user.id}/avatar/{avatar.id}/style_dna_input_{uuid.uuid4().hex[:8]}.wav"
                    await r2.upload_file(raw_audio, audio_key, content_type="audio/wav")
                    audio_url = r2.get_public_url(audio_key)
                    output_key = f"creators/{user.id}/avatar/{avatar.id}/style_dna_vocals_{uuid.uuid4().hex[:8]}.wav"
                    audio_size_bytes = os.path.getsize(raw_audio)
                    audio_duration_s = audio_size_bytes / (16000 * 2)  # mono 16-bit
                    gpu_result = await gpu_client.bs_roformer(
                        audio_url, output_key, max_duration=90,
                        input_duration_seconds=audio_duration_s,
                    )
                    vocals_url = gpu_result.get("vocals_url") if gpu_result else None
                    if vocals_url:
                        import httpx
                        local_clean = os.path.join(
                            "/tmp", f"styledna_vocals_{uuid.uuid4().hex[:8]}.wav",
                        )
                        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                            resp = await dl_client.get(vocals_url)
                            resp.raise_for_status()
                            with open(local_clean, "wb") as f:
                                f.write(resp.content)
                        clean_audio = local_clean
                        tmp_files.append(local_clean)
                        await log_usage(
                            db, user_id=user.id, event_type="voice_separation",
                            provider="hostkey", provider_cost_usd=0.0,
                            quantity=audio_duration_s, quantity_unit="audio_seconds",
                            resource_type="avatar", resource_id=avatar.id,
                        )
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    logger.warning(
                        "style_dna: voice separation failed for %s, using raw audio: %s",
                        url, exc,
                    )

            # 4. transcribe
            transcript_text = ""
            transcript_duration = 0.0
            if gpu_client is not None:
                # Whisper expects a public URL — upload (or reuse) the clean audio.
                transcribe_key = f"creators/{user.id}/avatar/{avatar.id}/style_dna_transcribe_{uuid.uuid4().hex[:8]}.wav"
                await r2.upload_file(clean_audio, transcribe_key, content_type="audio/wav")
                transcribe_url = r2.get_public_url(transcribe_key)
                whisper_result = await gpu_client.whisper_transcribe(transcribe_url)
                transcript_text = (whisper_result or {}).get("transcript", "") or ""
                transcript_duration = float((whisper_result or {}).get("duration_seconds", 0) or 0)
                await log_usage(
                    db, user_id=user.id, event_type="transcription",
                    provider="hostkey", provider_cost_usd=0.0,
                    quantity=transcript_duration, quantity_unit="audio_seconds",
                    resource_type="avatar", resource_id=avatar.id,
                )
            if not transcript_text:
                raise RuntimeError("transcription returned empty")

            survivors.append({
                "url": url,
                "audio_path": clean_audio,
                "transcript": transcript_text,
                "duration_s": transcript_duration,
            })
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("style_dna: video %s failed: %s", url, exc)
            continue

    if not survivors:
        # Best-effort cleanup
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail="Could not analyze any of the provided videos — try different URLs.",
        )

    # ── Step 5: combined voice → Fish Speech clone ──
    voice_id_new = None
    try:
        combined_audio, voice_duration_s = await _concat_audio_files(
            [s["audio_path"] for s in survivors]
        )
        tmp_files.append(combined_audio)

        fish = get_fish_audio_service()
        clone_name = f"styledna_{avatar.id}"
        joined_transcript = " ".join(s["transcript"] for s in survivors)
        voice_id_new = await fish.clone_voice_from_file(
            combined_audio, name=clone_name, transcript=joined_transcript[:1500],
        )
        if voice_id_new:
            await log_usage(
                db, user_id=user.id, event_type="voice_clone",
                provider="hostkey", provider_cost_usd=0.0,
                quantity=voice_duration_s, quantity_unit="audio_seconds",
                resource_type="avatar", resource_id=avatar.id,
            )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("style_dna: voice clone failed (continuing without): %s", exc)
        voice_duration_s = sum(s.get("duration_s", 0.0) for s in survivors)

    # ── Step 6: Claude Sonnet style analysis ──
    full_transcript = " ".join(s["transcript"] for s in survivors)
    style_prompt = f"""Analyze this creator's content style from their transcript.
TRANSCRIPT (from {len(survivors)} video(s), {voice_duration_s:.0f}s of speech):
{full_transcript[:3000]}

Analyze and return JSON:
{{
  "tone": "2-4 word description (e.g. 'energetic, casual, lots of questions')",
  "avg_energy": 7,
  "avg_sentence_length": 8,
  "common_phrases": ["top 5 phrases they repeat"],
  "sentence_starters": ["how they typically start sentences"],
  "sign_offs": ["how they end videos"],
  "question_frequency": 0.3,
  "hook_pattern": "pattern_interrupt | question | claim | story | shock",
  "cut_frequency_seconds": 3.5,
  "broll_ratio": 0.45,
  "caption_preset": "hormozi_bold | karaoke_pop | minimal_lower | etc",
  "preferred_transitions": ["whip_pan", "jump_cut"],
  "music_energy": "low | medium | high",
  "music_genre": "pop | lofi | electronic | acoustic | cinematic"
}}"""

    try:
        oai = get_openrouter_service()
        raw = await oai.generate_text(
            prompt=style_prompt,
            system_prompt="You are a video content analyst. Return valid JSON only.",
            model="anthropic/claude-sonnet-4",
            temperature=0.5,
        )
        usage = getattr(oai, "last_usage", {}) or {}
        if usage:
            try:
                input_tokens = int(usage.get("prompt_tokens", 0) or 0)
                output_tokens = int(usage.get("completion_tokens", 0) or 0)
                total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
                cost = calculate_llm_cost("anthropic/claude-sonnet-4", input_tokens, output_tokens)
                await log_usage(
                    db, user_id=user.id, event_type="style_dna_analysis",
                    provider="openrouter", provider_cost_usd=cost,
                    quantity=total_tokens, quantity_unit="tokens",
                    resource_type="avatar", resource_id=avatar.id,
                    provider_model="anthropic/claude-sonnet-4",
                )
            except Exception as inner_exc:
                sentry_sdk.capture_exception(inner_exc)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        # Tear down temp files before bailing
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="AI analysis temporarily unavailable")

    try:
        style_dna = json.loads(_strip_style_dna_json_fences(raw))
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="AI returned an unreadable analysis")

    for required_key in ("tone", "avg_energy", "common_phrases"):
        if required_key not in style_dna:
            sentry_sdk.capture_exception(
                ValueError(f"style_dna missing required key {required_key}")
            )
            for p in tmp_files:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail="AI returned an incomplete analysis")

    style_dna["voice_model_id"] = voice_id_new
    style_dna["voice_duration_s"] = round(voice_duration_s, 1)
    style_dna["source_urls"] = [s["url"] for s in survivors]
    style_dna["analyzed_at"] = datetime.utcnow().isoformat() + "Z"

    avatar.style_dna = style_dna
    if voice_id_new:
        # Style DNA owns the voice clone for this avatar — replaces any
        # earlier clone. Frontend warns the user before this point.
        avatar.voice_id = voice_id_new
    await db.commit()

    # Cleanup temp files (R2 uploads stay — they're cheap and aid debug).
    for p in tmp_files:
        try:
            os.unlink(p)
        except OSError:
            pass

    return {
        "style_dna": style_dna,
        "voice_duration_s": round(voice_duration_s, 1),
        "successful_videos": len(survivors),
        "requested_videos": len(urls),
    }


@router.delete("/{avatar_id}/style-dna")
async def reset_style_dna(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear Style DNA so the user can re-analyze with different videos.

    The avatar's voice clone (`voice_id`) is intentionally preserved — that
    column belongs to the avatar's identity, not to a particular Style DNA
    snapshot. Re-running analyze-style will overwrite it again.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    avatar.style_dna = None
    await db.commit()
    return {"ok": True}


@router.delete("/{avatar_id}", status_code=204)
async def delete_avatar(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default avatar")
    # Delete dependent rows to avoid FK violations
    try:
        from sqlalchemy import text as sa_text
        await db.execute(sa_text("DELETE FROM creator_voice_corpus WHERE avatar_id = :aid"), {"aid": avatar_id})
        await db.execute(sa_text("DELETE FROM body_shot_sets WHERE avatar_id = :aid"), {"aid": avatar_id})
    except Exception as cascade_err:
        import sentry_sdk
        sentry_sdk.capture_exception(cascade_err)
        logger.warning(f"Cascade delete failed for {avatar_id}: {cascade_err}")
    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.delete", entity_type="avatar",
            entity_id=avatar_id, before={"name": avatar.name, "type": str(avatar.type)},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
    await db.delete(avatar)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# AI AVATAR ENDPOINTS — Path 4
# ═══════════════════════════════════════════════════════════════════════

class CreateAIAvatarRequest(BaseModel):
    name: Optional[str] = "AI Avatar"

class CreateAIAvatarResponse(BaseModel):
    avatar_id: str


@router.post("/ai/create", response_model=CreateAIAvatarResponse, status_code=201)
async def create_ai_avatar(
    req: CreateAIAvatarRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an AI avatar record — no pipeline launched yet."""
    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=user.id,
        type=AvatarType.DIGITAL,
        status=AvatarStatus.PROCESSING,
        name=req.name or "AI Avatar",
        progress_step="Waiting for face and voice selection",
        progress_percent=0,
    )
    db.add(avatar)
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.create", entity_type="avatar",
            entity_id=avatar_id, after={"name": avatar.name, "type": "ai"},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return CreateAIAvatarResponse(avatar_id=avatar_id)


class GenerateFacesRequest(BaseModel):
    description: str = ""
    reference_photo_url: Optional[str] = None

class GenerateFacesResponse(BaseModel):
    face_urls: list[str]


@router.post("/ai/generate-faces", response_model=GenerateFacesResponse)
async def ai_generate_faces(
    req: GenerateFacesRequest,
    user: User = Depends(get_current_user),
):
    """Generate 8 face images using FLUX Kontext Pro."""
    import fal_client
    import os
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    from services.ai_prompts import get_prompt

    description = req.description or "A professional, friendly-looking person suitable for live streaming"

    base_prompt = description
    if req.reference_photo_url:
        base_prompt = f"{description}. Reference photo style."


    import asyncio
    face_urls = []

    async def generate_one(seed: int):
        try:
            args = {
                "prompt": base_prompt,
                "guidance_scale": 3.5,
                "num_inference_steps": 28,
                "output_format": "jpeg",
                "seed": seed,
            }
            if req.reference_photo_url:
                args["image_url"] = req.reference_photo_url

            model = "fal-ai/flux-pro/kontext" if req.reference_photo_url else "fal-ai/flux/dev"
            result = await fal_client.run_async(model, arguments=args)
            images = result.get("images", [])
            if images:
                return images[0].get("url", "")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Face generation failed for seed {seed}: {e}")
        return None

    import random
    seeds = [random.randint(1, 999999) for _ in range(8)]
    results = await asyncio.gather(*[generate_one(s) for s in seeds])
    face_urls = [url for url in results if url]

    if not face_urls:
        raise HTTPException(status_code=500, detail="Face generation failed. Please try again.")

    return GenerateFacesResponse(face_urls=face_urls)


class AIEditFaceRequest(BaseModel):
    face_url: str
    instructions: str

class AIEditFaceResponse(BaseModel):
    original_url: str
    edited_url: str


@router.post("/ai/edit-face", response_model=AIEditFaceResponse)
async def ai_edit_face(
    req: AIEditFaceRequest,
    user: User = Depends(get_current_user),
):
    """Edit a generated face using FLUX Kontext Pro."""
    import fal_client
    import os
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    from services.ai_prompts import get_prompt
    edit_prompt = f"{req.instructions}. Keep the same person identity and face structure."
    prompt = edit_prompt

    result = await fal_client.run_async(
        "fal-ai/flux-pro/kontext",
        arguments={
            "prompt": prompt,
            "image_url": req.face_url,
            "guidance_scale": 3.5,
            "num_inference_steps": 28,
            "output_format": "jpeg",
        },
    )

    images = result.get("images", [])
    if not images:
        raise HTTPException(status_code=500, detail="Face editing failed — no image returned")

    edited_url = images[0].get("url", "")
    if not edited_url:
        raise HTTPException(status_code=500, detail="Face editing returned empty URL")

    return AIEditFaceResponse(original_url=req.face_url, edited_url=edited_url)


class AISelectFaceRequest(BaseModel):
    face_url: str


@router.post("/ai/{avatar_id}/select-face")
async def ai_select_face(
    avatar_id: str,
    req: AISelectFaceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set the selected face URL as the avatar's face_ref_key."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    # Download the fal.ai image and re-upload to R2 for persistence
    import httpx
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(req.face_url)
            resp.raise_for_status()
            face_bytes = resp.content

        face_key = f"creators/{user.id}/avatar/{avatar_id}/face_ref.jpg"
        await r2.upload_bytes(face_bytes, face_key, "image/jpeg")
        avatar.face_ref_key = face_key
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        # Fall back to storing the URL directly
        avatar.face_ref_key = req.face_url
        await db.commit()
        logger.warning(f"Failed to persist face to R2: {e}")

    return {"status": "ok"}


class VoiceListResponse(BaseModel):
    voices: list[dict]
    total: int


@router.get("/ai/voices", response_model=VoiceListResponse)
async def ai_list_voices(
    page: int = 1,
    per_page: int = 6,
    gender: Optional[str] = None,
    search: Optional[str] = None,
    user: User = Depends(get_current_user),
):
    """List voices from Fish Audio voice library."""
    import httpx
    from config import settings as _settings

    params = {
        "page_size": per_page,
        "page_number": page,
        "sort_by": "task_count",
    }
    if search:
        params["title"] = search

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.fish.audio/model",
                params=params,
                headers={"Authorization": f"Bearer {_settings.FISH_AUDIO_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Voice library request failed: {e}")
        raise HTTPException(status_code=502, detail="Voice library temporarily unavailable")

    items = data.get("items", [])
    total = data.get("total", len(items))

    voices = []
    for item in items:
        # Filter by gender tag if specified
        raw_tags = item.get("tags", [])
        tags = [t if isinstance(t, str) else t.get("name", "") for t in raw_tags] if isinstance(raw_tags, list) else []
        item_gender = "unknown"
        for tag in tags:
            if tag.lower() in ("male", "man", "boy"):
                item_gender = "male"
                break
            elif tag.lower() in ("female", "woman", "girl"):
                item_gender = "female"
                break

        if gender and gender != "all" and item_gender != gender and item_gender != "unknown":
            continue

        sample_url = ""
        samples = item.get("samples", [])
        if samples and isinstance(samples, list):
            sample_url = samples[0].get("audio", "") or samples[0].get("url", "")

        voices.append({
            "voice_id": item.get("_id", ""),
            "name": item.get("title", "Unknown"),
            "description": item.get("description", ""),
            "gender": item_gender,
            "sample_url": sample_url,
            "tags": tags,
            "usage_count": item.get("task_count", 0),
        })

    return VoiceListResponse(voices=voices[:per_page], total=total)


class PreviewVoiceRequest(BaseModel):
    voice_id: str
    text: str
    speed: float = 1.0

class PreviewVoiceResponse(BaseModel):
    audio_url: str


@router.post("/ai/{avatar_id}/preview-voice", response_model=PreviewVoiceResponse)
async def ai_preview_voice(
    avatar_id: str,
    req: PreviewVoiceRequest,
    user: User = Depends(get_current_user),
):
    """Generate a TTS sample with a Fish Audio library voice."""
    from services.fish_audio import get_fish_audio_service
    from services.r2_storage import get_r2_storage_service

    fish = get_fish_audio_service()
    r2 = get_r2_storage_service()

    try:
        tts_result = await fish.generate_tts(text=req.text, voice_id=req.voice_id)
        tmp_path = tts_result["tmp_path"]
        import os
        preview_key = f"creators/{user.id}/avatar/{avatar_id}/voice_preview.mp3"
        await r2.upload_file(tmp_path, preview_key, content_type="audio/mpeg")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        audio_url = r2.get_public_url(preview_key, cache_bust=True)
        return PreviewVoiceResponse(audio_url=audio_url)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Voice preview generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice preview failed: {str(e)[:200]}")


class AISelectVoiceRequest(BaseModel):
    voice_id: str
    speed: float = 1.0


@router.post("/ai/{avatar_id}/select-voice")
async def ai_select_voice(
    avatar_id: str,
    req: AISelectVoiceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set the selected voice ID on the avatar."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    avatar.voice_id = req.voice_id
    await db.commit()
    return {"status": "ok"}


class AIGeneratePreviewRequest(BaseModel):
    test_script: Optional[str] = None


@router.post("/ai/{avatar_id}/generate-preview")
async def ai_generate_preview(
    avatar_id: str,
    req: AIGeneratePreviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate TTS + InfiniteTalk test video for AI avatar."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if not avatar.face_ref_key:
        raise HTTPException(status_code=400, detail="No face selected")
    if not avatar.voice_id:
        raise HTTPException(status_code=400, detail="No voice selected")

    if req.test_script:
        avatar.test_script = req.test_script
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = "Generating preview..."
    avatar.progress_percent = 10
    await db.commit()

    # Launch the generate_from_selection_task which handles TTS + InfiniteTalk
    from tasks.generate_avatar import generate_from_selection_task
    generate_from_selection_task.delay(avatar_id, user.id)

    return {"status": "ok", "message": "Preview generation started"}


async def _clone_voice_for_avatar(
    avatar: Avatar,
    user: User,
    audio_bytes: bytes,
    ext: str,
    content_type: str,
    db: AsyncSession,
) -> dict:
    """Upload an audio sample to R2 and clone a voice from it.

    Shared by the file-upload (`clone-voice`) and corpus-based
    (`clone-voice-from-corpus`) endpoints so both run the identical
    upload → clone → persist path.
    """
    from services.r2_storage import get_r2_storage_service
    from services.fish_audio import get_fish_audio_service

    r2 = get_r2_storage_service()
    fish = get_fish_audio_service()

    voice_sample_key = f"creators/{user.id}/avatar/{avatar.id}/voice_sample.{ext}"
    await r2.upload_bytes(audio_bytes, voice_sample_key, content_type)

    try:
        voice_url = r2.get_public_url(voice_sample_key)
        voice_id = await fish.clone_voice(voice_url, name=f"Custom voice for {avatar.name or avatar.id}")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Voice cloning failed: {str(e)[:200]}")

    avatar.voice_id = voice_id
    avatar.voice_sample_key = voice_sample_key
    await db.commit()

    return {"voice_id": voice_id, "voice_name": "Custom cloned voice"}


@router.post("/ai/{avatar_id}/clone-voice")
async def ai_clone_voice(
    avatar_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clone a voice from an uploaded audio sample for AI avatar."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    audio_bytes = await file.read()
    if len(audio_bytes) < 5000:
        raise HTTPException(status_code=400, detail="Audio file too small — need at least 30 seconds of speech")
    if len(audio_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio file too large — max 50 MB")

    ext = (file.filename or "audio.mp3").rsplit(".", 1)[-1].lower()
    return await _clone_voice_for_avatar(
        avatar, user, audio_bytes, ext, file.content_type or "audio/mpeg", db,
    )


class CloneVoiceFromCorpusRequest(BaseModel):
    # Accept a single id or a list — multiple ready samples are concatenated
    # into one training file so the user can pick several recordings.
    corpus_entry_id: Optional[str] = None
    corpus_entry_ids: Optional[list[str]] = None


# Minimum total speech required to train a usable voice clone. Mirrored by the
# frontend so the Train button stays disabled below this threshold.
MIN_VOICE_TRAIN_SECONDS = 8.0


@router.post("/ai/{avatar_id}/clone-voice-from-corpus")
async def ai_clone_voice_from_corpus(
    avatar_id: str,
    req: CloneVoiceFromCorpusRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clone a voice from one or more existing ready voice-corpus entries.

    Reuses audio already uploaded + isolated by the corpus pipeline, so the
    user can record several samples, pick which to use, and train without
    re-uploading. Multiple entries are concatenated into a single WAV.
    """
    import os
    import tempfile
    import subprocess

    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    ids: list[str] = []
    if req.corpus_entry_ids:
        ids.extend(req.corpus_entry_ids)
    if req.corpus_entry_id:
        ids.append(req.corpus_entry_id)
    # De-dupe while preserving order
    seen: set[str] = set()
    ids = [i for i in ids if not (i in seen or seen.add(i))]

    if not ids:
        raise HTTPException(status_code=400, detail="Provide corpus_entry_id or corpus_entry_ids")

    result = await db.execute(
        select(VoiceCorpusEntry).where(VoiceCorpusEntry.id.in_(ids))
    )
    found = {e.id: e for e in result.scalars().all()}

    entries: list[VoiceCorpusEntry] = []
    for entry_id in ids:
        entry = found.get(entry_id)
        if not entry or entry.avatar_id != avatar_id:
            raise HTTPException(status_code=404, detail=f"Voice corpus entry not found: {entry_id}")
        if entry.status != "ready":
            raise HTTPException(status_code=400, detail=f"Voice corpus entry not ready: {entry_id}")
        if not entry.audio_r2_key:
            raise HTTPException(status_code=400, detail=f"Voice corpus entry has no audio: {entry_id}")
        entries.append(entry)

    total_duration = sum((e.duration_seconds or 0.0) for e in entries)
    if total_duration < MIN_VOICE_TRAIN_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Need at least {int(MIN_VOICE_TRAIN_SECONDS)}s of voice — selected {total_duration:.0f}s",
        )

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    with tempfile.TemporaryDirectory() as tmpdir:
        local_paths: list[str] = []
        for idx, entry in enumerate(entries):
            local_path = os.path.join(tmpdir, f"sample_{idx}.wav")
            try:
                await r2.download_file(entry.audio_r2_key, local_path)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise HTTPException(status_code=502, detail=f"Failed to fetch audio for {entry.id}")
            local_paths.append(local_path)

        if len(local_paths) == 1:
            with open(local_paths[0], "rb") as f:
                audio_bytes = f.read()
        else:
            # Concatenate via ffmpeg concat demuxer (all inputs share format)
            concat_list = os.path.join(tmpdir, "concat.txt")
            with open(concat_list, "w") as f:
                for p in local_paths:
                    f.write(f"file '{p}'\n")
            merged_path = os.path.join(tmpdir, "merged.wav")
            try:
                subprocess.run(
                    ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list,
                     "-c", "copy", merged_path, "-y"],
                    check=True, capture_output=True,
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                # Fallback: re-encode (handles minor format drift between samples)
                try:
                    subprocess.run(
                        ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list,
                         "-ar", "16000", "-ac", "1", merged_path, "-y"],
                        check=True, capture_output=True,
                    )
                except Exception as e2:
                    sentry_sdk.capture_exception(e2)
                    raise HTTPException(status_code=500, detail="Failed to merge voice samples")
            with open(merged_path, "rb") as f:
                audio_bytes = f.read()

    return await _clone_voice_for_avatar(
        avatar, user, audio_bytes, "wav", "audio/wav", db,
    )


# ═══════════════════════════════════════════════════════════════════════
# NEW AI AVATAR ENDPOINTS — Pipeline rebuild (Phase A + B)
# ═══════════════════════════════════════════════════════════════════════


class GenerateDescriptionRequest(BaseModel):
    hint: str


@router.post("/ai/generate-description")
async def generate_description(
    req: GenerateDescriptionRequest,
    user: User = Depends(get_current_user),
):
    """Generate a detailed face description from a short hint.

    Uses OpenRouter LLM to expand a casual hint into a detailed,
    FLUX-optimized face description.

    Returns: {description: str, suggested_name: str}
    """
    import json as _json
    from services.openrouter import get_openrouter_service
    from services.ai_prompts import get_prompt

    openrouter = get_openrouter_service()
    prompt = get_prompt("face_description_generator")

    raw = await openrouter.generate_text(
        prompt=f"Create an avatar based on this idea: {req.hint}",
        system_prompt=prompt["system"],
        temperature=0.8,
    )

    # Parse response — expect JSON with description + suggested_name
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        cleaned = cleaned.strip()

    try:
        result = _json.loads(cleaned)
        return {
            "description": result.get("description", cleaned),
            "suggested_name": result.get("suggested_name", "Avatar"),
        }
    except (_json.JSONDecodeError, TypeError):
        return {"description": cleaned, "suggested_name": "Avatar"}


class GenerateVoiceDescriptionRequest(BaseModel):
    avatar_id: str
    # Optional base-voice traits the user picked in the step before. When present,
    # they MUST be reflected in the generated description — see ai_prompts.py.
    gender: str = ""
    language: str = ""
    accent: str = ""
    base_voice_id: str = ""
    base_voice_name: str = ""
    base_voice_descriptor: str = ""


# Mirrors generate-voice-previews so accent codes get resolved to the same
# human-readable labels the description prompt and ElevenLabs both understand.
_ACCENT_LABELS = {
    "us": "American English", "uk": "British English", "au": "Australian English",
    "ie": "Irish English", "in": "Indian English", "za": "South African English",
    "es": "Spain Spanish", "mx": "Mexican Spanish", "ar": "Argentinian Spanish",
    "co": "Colombian Spanish", "fr": "France French", "ca": "Canadian French",
    "de": "German", "br": "Brazilian Portuguese", "pt": "European Portuguese",
    "it": "Italian", "zh": "Mandarin Chinese", "yue": "Cantonese Chinese",
    "ja": "Japanese", "jp": "Japanese",
}


@router.post("/ai/generate-voice-description")
async def generate_voice_description(
    req: GenerateVoiceDescriptionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a voice description that matches the avatar's face description
    and the user's selected base-voice traits (gender, language, accent).

    Returns: {
        voice_description: str,
        test_speech: str,
        suggested_filters: {gender, language, tags}
    }
    """
    import json as _json
    from services.openrouter import get_openrouter_service
    from services.ai_prompts import get_prompt

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    openrouter = get_openrouter_service()
    prompt = get_prompt("voice_description_generator")

    # Build the user-message portion of the prompt. We always include the face
    # description and avatar name; we conditionally append base-voice context
    # so the prompt stays clean when the user hasn't picked anything yet.
    accent_label = _ACCENT_LABELS.get((req.accent or "").lower(), "")
    base_voice_lines: list[str] = []
    # Source-of-truth: prefer the user's request gender (just-picked), but
    # fall back to the avatar's stored gender so the LLM never invents one.
    effective_gender = (req.gender or (avatar.gender or "")).strip()
    if effective_gender:
        base_voice_lines.append(f"User-selected gender: {effective_gender}")
    if req.language:
        base_voice_lines.append(f"User-selected language: {req.language}")
    if accent_label:
        base_voice_lines.append(f"User-selected accent: {accent_label}")
    elif req.accent:
        # Pass through any unmapped accent code so the LLM still has the hint.
        base_voice_lines.append(f"User-selected accent: {req.accent}")
    if req.base_voice_name or req.base_voice_descriptor:
        descriptor_bits = [b for b in (req.base_voice_name, req.base_voice_descriptor) if b]
        base_voice_lines.append(
            "User-selected base voice: " + " — ".join(descriptor_bits)
        )

    # V1: thread the avatar's full identity (age, ethnicity, nationality)
    # into the prompt as hard constraints. The face description text already
    # encodes ethnicity/nationality (e.g. "Mixed Japanese-Brazilian") and we
    # surface age_range from target_audience separately so the LLM can
    # render an age-appropriate voice. Without these, the model regenerated
    # generic "young woman" descriptions that contradicted the visible
    # avatar profile.
    age_range = ""
    try:
        ta = avatar.target_audience or {}
        if isinstance(ta, dict):
            age_range = (ta.get("age_range") or "").strip()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        age_range = ""

    identity_lines: list[str] = []
    if age_range:
        identity_lines.append(f"Avatar age range: {age_range}")
    if effective_gender:
        identity_lines.append(f"Avatar gender: {effective_gender}")

    user_prompt = (
        f"Avatar face description: {avatar.appearance_prompt or avatar.description or 'A professional, friendly person'}\n"
        f"Avatar name: {avatar.name or 'Avatar'}"
    )
    if identity_lines:
        user_prompt += (
            "\n\nAvatar identity (treat as hard constraints — the voice description "
            "MUST match these, including any ethnicity / nationality cues from the "
            "face description above):\n" + "\n".join(identity_lines)
        )
    if base_voice_lines:
        user_prompt += "\n\nUser's base-voice picks (reflect these in the description):\n" + "\n".join(base_voice_lines)

    raw = await openrouter.generate_text(
        prompt=user_prompt,
        system_prompt=prompt["system"],
        temperature=0.7,
    )

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        cleaned = cleaned.strip()

    name = avatar.name or "Avatar"
    named_fallback = f"Hi, I'm {name}! Welcome to my stream — let me show you something amazing today!"

    # Default the suggested_filters to the user's picks when supplied so the UI
    # doesn't flip the user's gender/language pills back to defaults.
    fallback_filters = {
        "gender": req.gender or "female",
        "language": req.language or "english",
        "tags": [],
    }

    try:
        result = _json.loads(cleaned)
        description = result.get("voice_description", "Warm, friendly voice with clear pronunciation")
        logger.info(
            "voice_description.generate base_voice=%r accent=%r gender=%r language=%r len=%d",
            req.base_voice_id or req.base_voice_name or None,
            req.accent or None,
            req.gender or None,
            req.language or None,
            len(description),
        )
        return {
            "voice_description": description,
            "test_speech": result.get("test_speech", named_fallback),
            "suggested_filters": result.get("suggested_filters", fallback_filters),
        }
    except (_json.JSONDecodeError, TypeError) as e:
        sentry_sdk.capture_exception(e)
        logger.info(
            "voice_description.generate base_voice=%r accent=%r gender=%r language=%r len=%d (raw fallback)",
            req.base_voice_id or req.base_voice_name or None,
            req.accent or None,
            req.gender or None,
            req.language or None,
            len(cleaned),
        )
        return {
            "voice_description": cleaned,
            "test_speech": named_fallback,
            "suggested_filters": fallback_filters,
        }


class GenerateVoicePreviewsRequest(BaseModel):
    avatar_id: str
    voice_description: str
    test_speech: str = ""
    gender: str = ""
    language: str = ""
    accent: str = ""


@router.post("/ai/generate-voice-previews")
async def generate_voice_previews(
    req: GenerateVoicePreviewsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate 4 voice preview samples via ElevenLabs Voice Design.

    Returns: {previews: [{preview_id, audio_url, index}, ...]}
    """
    from services.elevenlabs import get_elevenlabs_service
    from services.r2_storage import get_r2_storage_service

    el = get_elevenlabs_service()
    r2 = get_r2_storage_service()

    # Fetch avatar name for personalized preview text
    avatar = await db.get(Avatar, req.avatar_id)
    avatar_name = avatar.name if avatar else "Avatar"

    # PR #65: append the mic-style suffix (clip-on lavalier vs phone mic)
    # so the voice-cloning engine generates a candidate that already
    # sounds like the user's chosen mic. Default = phone mic.
    try:
        from services.voice_postprocess_upload import apply_mic_style
        clip_mic_enabled = bool(getattr(avatar, "clip_mic_enabled", False))
        described = apply_mic_style(req.voice_description, clip_mic_enabled)
    except Exception as _mic_exc:
        sentry_sdk.capture_exception(_mic_exc)
        described = req.voice_description
    rich_description = f"High quality audio. {described}"

    # V2: gender-lock. The description text occasionally contradicts the
    # user's gender pick (e.g. "young woman" while gender=male) when the
    # LLM regenerated stale and the user manually changed gender after.
    # We aggressively normalise the description here so the upstream voice
    # engine cannot pick a wrong-gender candidate.
    effective_gender = (req.gender or "").strip().lower()
    if effective_gender in ("male", "female"):
        try:
            opposite = "female" if effective_gender == "male" else "male"
            opposite_tokens = {
                "female": [r"\bwoman\b", r"\bwomen\b", r"\bgirl\b", r"\bgirls\b", r"\blady\b", r"\bladies\b", r"\bshe\b", r"\bher\b", r"\bhers\b", r"\bfeminine\b"],
                "male":   [r"\bman\b", r"\bmen\b", r"\bboy\b", r"\bboys\b", r"\bguy\b", r"\bguys\b", r"\bgentleman\b", r"\bgentlemen\b", r"\bdude\b", r"\bdudes\b", r"\bhe\b", r"\bhis\b", r"\bhim\b", r"\bmasculine\b"],
            }[opposite]
            replacements = {
                "female": {"woman": "man", "women": "men", "girl": "boy", "girls": "boys", "lady": "gentleman", "ladies": "gentlemen", "she": "he", "her": "his", "hers": "his", "feminine": "masculine"},
                "male":   {"man": "woman", "men": "women", "boy": "girl", "boys": "girls", "guy": "lady", "guys": "ladies", "gentleman": "lady", "gentlemen": "ladies", "dude": "lady", "dudes": "ladies", "he": "she", "his": "her", "him": "her", "masculine": "feminine"},
            }[opposite]
            for pattern in opposite_tokens:
                # Case-insensitive replace, preserve the swap target as lowercase.
                token_match = re.compile(pattern, flags=re.IGNORECASE)
                def _swap(m, _opp=opposite):
                    word = m.group(0).lower()
                    return replacements.get(word, word)
                rich_description = token_match.sub(_swap, rich_description)
        except Exception as e:
            sentry_sdk.capture_exception(e)

    # Prepend gender authoritatively so upstream filters / heuristics see it
    # at the very front of the prompt (most engines weight head tokens).
    if req.gender and req.gender.lower() not in rich_description.lower():
        rich_description = f"{req.gender.capitalize()} voice. {rich_description}"
    elif req.gender:
        # Even if the gender word already appears, re-prepend a leading
        # "Male voice." / "Female voice." so the directive is unambiguous.
        rich_description = f"{req.gender.capitalize()} voice. {rich_description}"

    # Use accent (specific) over language (generic) for accent directive.
    # Shares the _ACCENT_LABELS map with generate-voice-description so both
    # endpoints render the user's accent pick identically.
    accent_label = _ACCENT_LABELS.get((req.accent or "").lower(), "")
    if accent_label:
        rich_description = f"{rich_description}. Speaking with a clear {accent_label} accent."
    elif req.language and req.language.lower() not in rich_description.lower():
        rich_description = f"{rich_description}. {req.language.capitalize()} accent."

    # Prepend audio quality hint (ElevenLabs responds well to this)
    rich_description = f"High quality audio. {rich_description}"

    # Build personalized fallback text using avatar's name
    fallback_text = f"Hi, I'm {avatar_name}! Welcome to my stream — I've got some amazing products to show you today!"
    preview_text = req.test_speech or fallback_text
    logger.info(f"Voice preview request: rich_description='{rich_description}', text='{preview_text}'")

    previews = await el.generate_voice_previews(
        description=rich_description,
        text=preview_text,
    )

    result_previews = []
    for p in previews:
        audio_bytes = base64.b64decode(p["audio_base_64"])
        r2_key = f"creators/{user.id}/avatar/{req.avatar_id}/voice_preview_{p['index']}.mp3"
        await r2.upload_bytes(audio_bytes, r2_key, "audio/mpeg")

        result_previews.append({
            "preview_id": p["preview_id"],
            "audio_url": r2.get_public_url(r2_key, cache_bust=True),
            "index": p["index"],
        })

    return {"previews": result_previews}


class ApproveVoiceRequest(BaseModel):
    avatar_id: str
    preview_id: str


@router.post("/ai/approve-voice")
async def approve_voice(
    req: ApproveVoiceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a voice preview and train Fish Audio with it.

    Pipeline:
    1. ElevenLabs generates a 45-second training sample with the approved voice
    2. Upload training sample to R2
    3. Send to Fish Audio voice clone endpoint
    4. Fish Audio returns a voice_id (model ID)
    5. Store voice_id on the avatar — this is the permanent voice
    """
    import tempfile
    import os
    from services.elevenlabs import get_elevenlabs_service, VOICE_TRAINING_TEXT
    from services.fish_audio import get_fish_audio_service
    from services.r2_storage import get_r2_storage_service

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    el = get_elevenlabs_service()
    fish = get_fish_audio_service()
    r2 = get_r2_storage_service()

    if avatar.voice_id:
        logger.info(f"Avatar {avatar.id} already has voice_id={avatar.voice_id}, skipping ElevenLabs re-conversion")
        voice_id_el = avatar.voice_id
    else:
        try:
            voice_id_el = await el.create_voice_from_preview(
                req.preview_id,
                avatar.name or "AI Avatar",
                avatar.description or "AI generated voice",
            )
            avatar.voice_id = voice_id_el
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"ElevenLabs create voice from preview failed: {e}")
            raise HTTPException(status_code=500, detail=f"Voice creation from preview failed: {str(e)[:200]}")

    # Step 0: Convert preview to permanent voice
    try:
        voice_id_el = await el.create_voice_from_preview(
            req.preview_id,
            avatar.name or "AI Avatar",
            avatar.description or "AI generated voice",
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"ElevenLabs create voice from preview failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice creation from preview failed: {str(e)[:200]}")

    # Step 1: Generate 45-second training sample with the permanent voice
    try:
        training_audio = await el.generate_training_sample(voice_id_el)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"ElevenLabs training sample failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice training sample generation failed: {str(e)[:200]}")

    # Step 2: Save to temp file + R2
    tmp_path = os.path.join(tempfile.gettempdir(), f"voice_training_{req.avatar_id}.mp3")
    with open(tmp_path, "wb") as f:
        f.write(training_audio)

    training_key = f"creators/{user.id}/avatar/{req.avatar_id}/voice_training_sample.mp3"
    await r2.upload_bytes(training_audio, training_key, "audio/mpeg")

    # Step 3: Clone with Fish Audio
    try:
        voice_id = await fish.clone_voice_from_file(
            tmp_path,
            name=f"AI Avatar voice for {avatar.name or req.avatar_id}",
            transcript=VOICE_TRAINING_TEXT,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Voice cloning failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice cloning failed: {str(e)[:200]}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    try:
        await el.delete_voice(voice_id_el)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Failed to delete ElevenLabs voice {voice_id_el} after Fish Audio clone: {e}")

    # Step 4: Store on avatar
    avatar.voice_id = voice_id
    avatar.voice_sample_key = training_key
    await db.commit()

    return {"voice_id": voice_id, "status": "Voice trained and locked to this avatar"}


# ═══════════════════════════════════════════════════════════════
# Avatar Pipeline Overhaul — new endpoints (A2)
# ═══════════════════════════════════════════════════════════════


class RewriteDescriptionRequest(BaseModel):
    avatar_id: str
    target_audience: Optional[dict] = None
    base_description: str = ""
    style_presets: list[str] = []
    imperfections: list[str] = []
    regenerate: bool = False


STYLE_PRESET_PHRASES = {
    "studio": "professional studio lighting, clean background",
    "studio_pro": "professional studio lighting, flawless polished skin, clean white background, magazine-quality portrait",
    "natural": "natural daylight, organic feel",
    "natural_real": "natural daylight, realistic skin texture with pores, candid amateur photograph",
    "cinematic": "cinematic film still, dramatic rim lighting, shallow depth of field, moody color grading",
    "stylized": "artistic stylized look, fashion-forward",
    "stylized_cartoon": "anime art style, vibrant colors, cel-shaded, digital illustration",
    "street": "urban street photography, candid raw aesthetic, gritty textures, bokeh city background",
    "glamour": "high-fashion glamour photography, bold makeup, dramatic contouring, luxury lighting",
    "editorial": "editorial magazine spread, soft diffused lighting, neutral tones, minimal background",
    "golden_hour": "golden hour warm sunset lighting, dreamy soft glow, warm amber tones",
    "anime": "anime art style, Japanese animation, vibrant saturated colors, cel-shaded, large expressive eyes",
    "cyberpunk": "neon-lit cyberpunk aesthetic, futuristic, holographic accents, dark urban backdrop with neon reflections",
    "vintage_film": "vintage film grain, 70s-90s film photography, warm desaturated tones, retro color palette",
    "soft_beauty": "dewy K-beauty aesthetic, glass skin, soft pastel tones, gentle diffused lighting, luminous glow",
}

IMPERFECTION_PHRASES = {
    "freckles": "light freckles across the nose and cheeks",
    "slight_asymmetry": "slightly asymmetric facial features for realism",
    "skin_texture": "visible skin texture and pores",
    "laugh_lines": "subtle laugh lines around the eyes",
    "bushy_brows": "naturally full, slightly unruly eyebrows",
    "gap_teeth": "small charming gap between front teeth",
    "phone_selfie": "shot on smartphone camera, slight grain, natural uneven lighting",
    "tired_after_work": "slightly fatigued face, subtle under-eye circles, end-of-day look",
    "bare_face": "zero makeup, natural bare skin, visible pores",
    "outdoor_light": "natural daylight outdoors, dappled sunlight",
    "morning_look": "just woke up energy, slightly disheveled hair, cozy and approachable",
    "weathered": "sun-kissed skin with texture, freckles, slight tan lines",
    "office_casual": "slightly loosened collar, rolled-up sleeves, professional but human",
    "asymmetric": "natural facial asymmetry, one eyebrow slightly higher",
    "natural_pores": "visible natural skin pores, unretouched complexion",
    "stray_hairs": "a few stray hairs, not perfectly styled, natural and lived-in",
    "post_workout": "slight sheen of sweat, flushed healthy complexion, post-exercise glow",
}


@router.post("/ai/rewrite-description")
async def rewrite_description(
    req: RewriteDescriptionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite avatar description. Chip toggles are instant (template-based).
    LLM call only when regenerate=true (Surprise Me / Regenerate)."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        # Save target audience if provided
        if req.target_audience:
            avatar.target_audience = req.target_audience
            await db.commit()

        if req.regenerate:
            # LLM path — use Gemini/OpenRouter for creative rewrite
            from services.openrouter import get_openrouter_service
            from services.ai_prompts import get_prompt

            oai = get_openrouter_service()
            prompt_data = get_prompt("gemini_avatar_description_rewrite")
            audience_context = ""
            if req.target_audience:
                ta = req.target_audience
                audience_context = f"\nTarget audience: {ta.get('age_range', 'general')}, interests: {', '.join(ta.get('interests', []))}, {ta.get('description', '')}"

            user_msg = f"Base description: {req.base_description}\nStyle presets: {', '.join(req.style_presets)}\nMake it real details: {', '.join(req.imperfections)}{audience_context}\n\nRewrite this into a vivid, detailed avatar description."

            log_creative_model_use("avatar_description_rewrite", CREATIVE_DESCRIPTION_MODEL)
            description = await oai.generate_text(
                prompt=user_msg,
                system_prompt=prompt_data["system"],
                model=CREATIVE_DESCRIPTION_MODEL,
                max_tokens=500,
            )
            description = description.strip()

            try:
                from services.usage_tracker import calculate_llm_cost, log_usage
                _u = getattr(oai, "last_usage", {}) or {}
                if _u:
                    await log_usage(
                        db,
                        user_id=user.id,
                        event_type="script_generation",
                        provider="openrouter",
                        provider_cost_usd=calculate_llm_cost(
                            CREATIVE_DESCRIPTION_MODEL,
                            int(_u.get("prompt_tokens") or 0),
                            int(_u.get("completion_tokens") or 0),
                        ),
                        quantity=int(_u.get("total_tokens") or 0),
                        quantity_unit="tokens",
                        resource_type="avatar",
                        resource_id=avatar.id,
                        provider_model=CREATIVE_DESCRIPTION_MODEL,
                    )
                    await db.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)
        else:
            # Template path — deterministic, instant
            parts = [req.base_description.strip()]
            for preset in req.style_presets:
                phrase = STYLE_PRESET_PHRASES.get(preset)
                if phrase:
                    parts.append(phrase)
            for imp in req.imperfections:
                phrase = IMPERFECTION_PHRASES.get(imp)
                if phrase:
                    parts.append(phrase)
            description = ". ".join(p.rstrip(".") for p in parts if p) + "."

        avatar.description = description
        await db.commit()

        return {"description": description}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"rewrite-description failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


class GenerateBodyDescriptionRequest(BaseModel):
    avatar_id: str


@router.post("/ai/generate-body-description")
async def generate_body_description(
    req: GenerateBodyDescriptionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a body description from avatar description + target audience.
    Uses LLM to create a consistent body description anchored to the face description."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        if not avatar.face_ref_key:
            raise HTTPException(status_code=400, detail="Face image required before body description")

        from services.openrouter import get_openrouter_service
        from services.ai_prompts import get_prompt

        oai = get_openrouter_service()
        prompt_data = get_prompt("gemini_body_description")

        audience_context = ""
        if avatar.target_audience:
            ta = avatar.target_audience
            audience_context = f"\nTarget audience: {ta.get('age_range', 'general')}, interests: {', '.join(ta.get('interests', []))}, {ta.get('description', '')}"

        # Propagate gender, age range, and other demographic context into the prompt
        demographics = []
        if avatar.gender:
            demographics.append(f"Gender: {avatar.gender}")
        if avatar.target_audience and avatar.target_audience.get("age_range"):
            demographics.append(f"Age range: {avatar.target_audience['age_range']}")
        if avatar.style:
            demographics.append(f"Style: {avatar.style}")
        demographics_str = ("\n" + "\n".join(demographics)) if demographics else ""

        user_msg = f"Avatar description: {avatar.description or 'A professional content creator'}{audience_context}{demographics_str}\n\nDescribe this person's full body appearance for consistent multi-angle image generation."

        log_creative_model_use("avatar_body_description", CREATIVE_DESCRIPTION_MODEL)
        body_desc = await oai.generate_text(
            prompt=user_msg,
            system_prompt=prompt_data["system"],
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=600,
        )

        try:
            from services.usage_tracker import calculate_llm_cost, log_usage
            _u = getattr(oai, "last_usage", {}) or {}
            if _u:
                await log_usage(
                    db,
                    user_id=user.id,
                    event_type="script_generation",
                    provider="openrouter",
                    provider_cost_usd=calculate_llm_cost(
                        CREATIVE_DESCRIPTION_MODEL,
                        int(_u.get("prompt_tokens") or 0),
                        int(_u.get("completion_tokens") or 0),
                    ),
                    quantity=int(_u.get("total_tokens") or 0),
                    quantity_unit="tokens",
                    resource_type="avatar",
                    resource_id=avatar.id,
                    provider_model=CREATIVE_DESCRIPTION_MODEL,
                )
                await db.commit()
        except Exception as _exc:
            sentry_sdk.capture_exception(_exc)
        body_desc = body_desc.strip()

        avatar.body_description = body_desc
        await db.commit()

        return {"body_description": body_desc}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"generate-body-description failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


class GenerateBodyShotsRequest(BaseModel):
    avatar_id: str


async def _run_body_shots_pipeline(set_id: str, avatar_id: str, user_id: str) -> None:
    """Background worker that runs the full body-shot pipeline.

    The HTTP endpoint creates the BodyShotSet row with status='running' and
    returns immediately to avoid Cloudflare's ~100s edge timeout. This worker
    runs on the orchestrator event loop, opens its own DB session, executes
    the full Stage 1 + Stage 2 + validation pipeline, and updates the row to
    status='completed' or status='failed' when done.

    The frontend polls GET /ai/body-shot-sets/{set_id} for state.
    """
    import os
    import random
    import fal_client
    import httpx
    from config import settings as _settings
    from database import async_session_factory
    from services.r2_storage import get_r2_storage_service
    from services.ai_prompts import get_prompt
    from services.openrouter import get_openrouter_service
    from models.avatar import BodyShotSet

    async def _mark_failed(message: str) -> None:
        try:
            async with async_session_factory() as fs:
                row = await fs.get(BodyShotSet, set_id)
                if row is not None:
                    row.status = "failed"
                    row.error_message = message[:500]
                    await fs.commit()
        except Exception as inner:
            sentry_sdk.capture_exception(inner)
            logger.error(f"_mark_failed failed for set {set_id}: {inner}")

    db = async_session_factory()
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar:
            await _mark_failed("Avatar not found")
            return

        r2 = get_r2_storage_service()
        if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
            os.environ["FAL_KEY"] = _settings.FAL_API_KEY

        body_desc = avatar.body_description
        style_hint = ""
        if avatar.target_audience and isinstance(avatar.target_audience, dict):
            presets = avatar.target_audience.get("style_presets", [])
            if presets:
                style_hint = f"Style: {', '.join(presets)}"

        # ── Pre-Stage-1: Vision-extract wardrobe from the SELECTED face image ──
        # The setup-time body_description was generated before the user picked a
        # face, so its clothing/glasses fields are stale once they edit or pick a
        # different look. Source-of-truth for wardrobe must be the actual face
        # image at avatar.face_ref_key — that's what the user sees when they
        # hit "approve face". We extract wardrobe + has_glasses via the vision
        # LLM and use that as the clothing string for Stage 1, OVERRIDING any
        # clothing sentences found in body_description. Body attributes
        # (build/height/posture) still come from body_description.
        face_ref_url = r2.get_public_url(avatar.face_ref_key)
        face_wardrobe_summary: str | None = None
        face_wardrobe_extracted: dict | None = None
        try:
            extractor_prompt_data = get_prompt("face_wardrobe_extractor")
            openrouter_for_wardrobe = get_openrouter_service()
            face_ref_public = r2.get_public_url(avatar.face_ref_key, cache_bust=True)
            raw_wardrobe = await openrouter_for_wardrobe.describe_image(
                image_url=face_ref_public,
                system_prompt=extractor_prompt_data["system"],
                user_text=(
                    "Extract the wardrobe and accessories the person is wearing in this image. "
                    "Pay special attention to glasses (yes/no) and the top garment (suit, blazer, "
                    "hoodie, t-shirt, dress, etc.). Respond with ONLY the JSON object."
                ),
                model=CREATIVE_DESCRIPTION_MODEL,
                max_tokens=512,
                temperature=0.0,
            )
            try:
                from services.usage_tracker import calculate_llm_cost, log_usage
                _u = getattr(openrouter_for_wardrobe, "last_usage", {}) or {}
                if _u:
                    async with async_session_factory() as _usg_session:
                        await log_usage(
                            _usg_session,
                            user_id=user_id,
                            event_type="vision_check",
                            provider="openrouter",
                            provider_cost_usd=calculate_llm_cost(
                                CREATIVE_DESCRIPTION_MODEL,
                                int(_u.get("prompt_tokens") or 0),
                                int(_u.get("completion_tokens") or 0),
                            ),
                            quantity=int(_u.get("total_tokens") or 0),
                            quantity_unit="tokens",
                            resource_type="avatar",
                            resource_id=avatar.id,
                            provider_model=CREATIVE_DESCRIPTION_MODEL,
                        )
                        await _usg_session.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)
            cleaned_w = raw_wardrobe.strip()
            if cleaned_w.startswith("```"):
                _lines = cleaned_w.splitlines()
                cleaned_w = (
                    "\n".join(_lines[1:-1])
                    if _lines and _lines[-1].strip() == "```"
                    else "\n".join(_lines[1:])
                )
            import json as _json_w
            face_wardrobe_extracted = _json_w.loads(cleaned_w)
            summary = (face_wardrobe_extracted.get("wardrobe_summary") or "").strip()
            has_glasses = bool(face_wardrobe_extracted.get("has_glasses"))
            glasses_desc = (face_wardrobe_extracted.get("glasses_description") or "").strip()
            if has_glasses and glasses_desc and "glasses" not in summary.lower():
                summary = f"{summary} wearing {glasses_desc}.".strip()
            elif (not has_glasses) and ("no glasses" not in summary.lower()):
                summary = f"{summary} No glasses, no eyewear.".strip()
            face_wardrobe_summary = summary or None
            logger.info(
                f"Vision wardrobe extracted for avatar {avatar.id}: "
                f"has_glasses={has_glasses}, summary={summary[:120]}"
            )
        except Exception as wardrobe_exc:
            sentry_sdk.capture_exception(wardrobe_exc)
            logger.warning(
                f"Face wardrobe extraction failed for avatar {avatar.id}: {wardrobe_exc} — "
                "falling back to body_description clothing sentences."
            )

        # Extract clothing-only sentences from body_desc as a FALLBACK only.
        # body_description contains body type, posture, personality, facial features
        # AND clothing. Only the clothing matters for Stage 1 — identity comes from
        # the face_ref image. Long descriptive text overrides Kontext Max's identity
        # preservation, so we keep it minimal: clothing + accessories only.
        _clothing_kw = re.compile(
            r"\b(?:wear(?:s|ing)?|blous\w*|shirts?|sweaters?|jackets?|coats?"
            r"|dress(?:es)?|skirts?|pants|trousers|jeans|shorts"
            r"|shoes|sneakers|boots|heels|sandals"
            r"|necklaces?|earrings?|bracelets?|watch(?:es)?"
            r"|glasses|sunglasses|pendants?|hoops?"
            r"|hats?|scarves?|scarfs?|belts?|outfits?|clothing"
            r"|cotton|silk|linen|denim|leather|wool|knit"
            r"|sleeves?|cardigan|hoodie)\b",
            re.IGNORECASE,
        )
        _desc_sentences = re.split(r"(?<=[.!?])\s+", body_desc.strip()) if body_desc else []
        _clothing_sentences = [s for s in _desc_sentences if _clothing_kw.search(s)]
        body_desc_clothing_fallback = " ".join(_clothing_sentences) if _clothing_sentences else (body_desc or "")

        # Vision-extracted wardrobe is source of truth. body_description fallback
        # is only used when the vision pass fails.
        body_desc_clothing = face_wardrobe_summary or body_desc_clothing_fallback

        # body_desc_for_angles is what gets fed into Stage 2 per-angle prompts
        # AND surfaced to the UI via BodyShotSet.description_used. When the
        # vision pass succeeded we use ONLY the grounded wardrobe summary —
        # the original body_description is a creative-writing string from
        # avatar setup that often hallucinates accessories ("messenger bag with
        # enamel pins", "leather-bound journal") that are not in the actual
        # photo, and concatenating it back in defeats the purpose of the
        # vision pre-pass and produces hallucinated body shots.
        if face_wardrobe_summary:
            body_desc_for_angles = face_wardrobe_summary
        else:
            body_desc_for_angles = body_desc or ""

        locked_seed = random.randint(1, 999999)
        # set_id is created by the HTTP endpoint and passed in

        # ── Stage 1: Canonical full-body front via FLUX Kontext Max (face-anchored) ──
        # Uses avatar's face_ref as image_url so identity is preserved.
        # Kontext Max preserves identity (hair, eyes, face) from the reference
        # while applying clothing from the prompt. Previous attempts with
        # kontext (non-max) and flux-pro/v1.1-ultra lost identity.
        # face_ref_url already resolved above (used by the wardrobe extractor).
        canonical_prompt_data = get_prompt("flux_body_canonical_front")
        canonical_prompt = canonical_prompt_data["system"].format(
            body_description=body_desc_clothing,
        )

        async def _generate_canonical(prompt_text: str, seed: int) -> bytes | None:
            logger.info(
                f"Stage 1: Generating canonical front for avatar {avatar.id} via Kontext Max "
                f"(seed={seed}, prompt_chars={len(prompt_text)})"
            )
            # safety_tolerance=5 unlocks the highest available detail without
            # changing the model. Keep JPEG to match the .jpg storage key.
            result = await fal_client.run_async(
                "fal-ai/flux-pro/kontext/max",
                arguments={
                    "prompt": prompt_text,
                    "image_url": face_ref_url,
                    "num_images": 1,
                    "output_format": "jpeg",
                    "seed": seed,
                    "aspect_ratio": "9:16",
                    "safety_tolerance": "5",
                },
            )
            imgs = result.get("images", [])
            if not imgs:
                return None
            async with httpx.AsyncClient() as cli:
                resp = await cli.get(imgs[0]["url"], timeout=60)
                resp.raise_for_status()
                return resp.content

        canonical_bytes = await _generate_canonical(canonical_prompt, locked_seed)
        if not canonical_bytes:
            await _mark_failed("Stage 1 failed: no canonical image generated")
            return

        try:
            from services.usage_tracker import calculate_fal_image_cost, log_usage
            async with async_session_factory() as _usg_session:
                await log_usage(
                    _usg_session,
                    user_id=user_id,
                    event_type="body_shot_generation",
                    provider="fal_ai",
                    provider_cost_usd=calculate_fal_image_cost("kontext/max", 1),
                    quantity=1,
                    quantity_unit="images",
                    resource_type="avatar",
                    resource_id=avatar.id,
                    provider_model="flux-pro/kontext/max",
                )
                await _usg_session.commit()
        except Exception as _exc:
            sentry_sdk.capture_exception(_exc)

        canonical_key = f"creators/{user_id}/avatar/{avatar.id}/body_shots/{set_id}/canonical.jpg"
        await r2.upload_bytes(canonical_bytes, canonical_key, "image/jpeg")
        canonical_url = r2.get_public_url(canonical_key)

        logger.info(f"Stage 1 complete: canonical saved to {canonical_key}")

        # ── Stage 1.5: AI clothing-consistency check ──
        # Sends face reference + canonical body shot to a vision LLM. Asks whether
        # the clothing/hair/accessories match. If not, regenerates the canonical
        # ONCE with a more explicit prompt that enumerates the discrepancies.
        # After one retry we accept whatever we got (logging mismatches via
        # Sentry) so the user is never blocked.
        clothing_consistency_warning: str | None = None
        clothing_check: dict | None = None
        try:
            check_prompt_data = get_prompt("body_shot_clothing_consistency_check")
            openrouter_for_check = get_openrouter_service()
            face_ref_public = r2.get_public_url(avatar.face_ref_key, cache_bust=True)
            canonical_public = r2.get_public_url(canonical_key, cache_bust=True)

            user_msg = (
                "Image 1 is the face reference photo. Image 2 is the generated body shot. "
                "Does the person in image 2 wear the same clothing, hairstyle, and accessories "
                "as the person in image 1? Answer with the JSON schema you were given."
            )
            raw = await openrouter_for_check.compare_two_images(
                image_url_a=face_ref_public,
                image_url_b=canonical_public,
                system_prompt=check_prompt_data["system"],
                user_text=user_msg,
                model="anthropic/claude-sonnet-4",
                max_tokens=512,
                temperature=0.0,
            )
            try:
                from services.usage_tracker import calculate_llm_cost, log_usage
                _u = getattr(openrouter_for_check, "last_usage", {}) or {}
                if _u:
                    async with async_session_factory() as _usg_session:
                        await log_usage(
                            _usg_session,
                            user_id=user_id,
                            event_type="vision_check",
                            provider="openrouter",
                            provider_cost_usd=calculate_llm_cost(
                                "anthropic/claude-sonnet-4",
                                int(_u.get("prompt_tokens") or 0),
                                int(_u.get("completion_tokens") or 0),
                            ),
                            quantity=int(_u.get("total_tokens") or 0),
                            quantity_unit="tokens",
                            resource_type="avatar",
                            resource_id=avatar.id,
                            provider_model="anthropic/claude-sonnet-4",
                        )
                        await _usg_session.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                _lines = cleaned.splitlines()
                cleaned = (
                    "\n".join(_lines[1:-1])
                    if _lines and _lines[-1].strip() == "```"
                    else "\n".join(_lines[1:])
                )
            try:
                import json as _json
                parsed = _json.loads(cleaned)
            except Exception as parse_exc:
                sentry_sdk.capture_exception(parse_exc)
                logger.warning(f"AI clothing check returned non-JSON: {raw[:200]}")
                parsed = {"matches": True, "discrepancies": []}

            matches = bool(parsed.get("matches", True))
            discrepancies = parsed.get("discrepancies") or []
            if not isinstance(discrepancies, list):
                discrepancies = [str(discrepancies)]
            clothing_check = {
                "matches": matches,
                "discrepancies": discrepancies,
                "retry_attempted": False,
            }
            logger.info(
                f"AI clothing check (set {set_id}): matches={matches}, "
                f"discrepancies={discrepancies}"
            )

            if not matches:
                # One explicit retry with the discrepancies fed back into the prompt.
                discrepancy_text = "; ".join(str(d) for d in discrepancies) or "clothing or accessories did not match the reference photo"
                retry_prompt = (
                    f"{canonical_prompt}\n\n"
                    f"The previous attempt got these things wrong: {discrepancy_text}. "
                    f"Generate again, paying special attention to those details and matching the reference photo exactly."
                )
                retry_seed = random.randint(1, 999999)
                logger.info(
                    f"AI clothing check failed for set {set_id} — regenerating canonical once "
                    f"(seed={retry_seed})"
                )
                retry_bytes = await _generate_canonical(retry_prompt, retry_seed)
                if retry_bytes:
                    try:
                        from services.usage_tracker import calculate_fal_image_cost, log_usage
                        async with async_session_factory() as _usg_session:
                            await log_usage(
                                _usg_session,
                                user_id=user_id,
                                event_type="body_shot_generation",
                                provider="fal_ai",
                                provider_cost_usd=calculate_fal_image_cost("kontext/max", 1),
                                quantity=1,
                                quantity_unit="images",
                                resource_type="avatar",
                                resource_id=avatar.id,
                                provider_model="flux-pro/kontext/max",
                            )
                            await _usg_session.commit()
                    except Exception as _exc:
                        sentry_sdk.capture_exception(_exc)
                    await r2.upload_bytes(retry_bytes, canonical_key, "image/jpeg")
                    canonical_url = r2.get_public_url(canonical_key)
                    canonical_public_retry = r2.get_public_url(canonical_key, cache_bust=True)
                    # Re-check after the retry, but accept whatever we get.
                    try:
                        raw2 = await openrouter_for_check.compare_two_images(
                            image_url_a=face_ref_public,
                            image_url_b=canonical_public_retry,
                            system_prompt=check_prompt_data["system"],
                            user_text=user_msg,
                            model="anthropic/claude-sonnet-4",
                            max_tokens=512,
                            temperature=0.0,
                        )
                        try:
                            from services.usage_tracker import calculate_llm_cost, log_usage
                            _u2 = getattr(openrouter_for_check, "last_usage", {}) or {}
                            if _u2:
                                async with async_session_factory() as _usg_session:
                                    await log_usage(
                                        _usg_session,
                                        user_id=user_id,
                                        event_type="vision_check",
                                        provider="openrouter",
                                        provider_cost_usd=calculate_llm_cost(
                                            "anthropic/claude-sonnet-4",
                                            int(_u2.get("prompt_tokens") or 0),
                                            int(_u2.get("completion_tokens") or 0),
                                        ),
                                        quantity=int(_u2.get("total_tokens") or 0),
                                        quantity_unit="tokens",
                                        resource_type="avatar",
                                        resource_id=avatar.id,
                                        provider_model="anthropic/claude-sonnet-4",
                                    )
                                    await _usg_session.commit()
                        except Exception as _exc:
                            sentry_sdk.capture_exception(_exc)
                        cleaned2 = raw2.strip()
                        if cleaned2.startswith("```"):
                            _lines2 = cleaned2.splitlines()
                            cleaned2 = (
                                "\n".join(_lines2[1:-1])
                                if _lines2 and _lines2[-1].strip() == "```"
                                else "\n".join(_lines2[1:])
                            )
                        import json as _json
                        parsed2 = _json.loads(cleaned2)
                        matches2 = bool(parsed2.get("matches", True))
                        discrepancies2 = parsed2.get("discrepancies") or []
                        if not isinstance(discrepancies2, list):
                            discrepancies2 = [str(discrepancies2)]
                        clothing_check = {
                            "matches": matches2,
                            "discrepancies": discrepancies2,
                            "retry_attempted": True,
                            "first_attempt_discrepancies": discrepancies,
                        }
                        if not matches2:
                            clothing_consistency_warning = "clothing_mismatch_after_retry"
                            sentry_sdk.capture_message(
                                f"AI clothing check still failing after retry for set {set_id}: {discrepancies2}",
                                level="warning",
                            )
                    except Exception as recheck_exc:
                        sentry_sdk.capture_exception(recheck_exc)
                        logger.warning(f"Re-check after retry failed: {recheck_exc}")
                        clothing_check = {
                            "matches": False,
                            "discrepancies": discrepancies,
                            "retry_attempted": True,
                            "recheck_error": str(recheck_exc)[:200],
                        }
                        clothing_consistency_warning = "clothing_recheck_failed"
                else:
                    sentry_sdk.capture_message(
                        f"AI clothing-check retry generation failed for set {set_id}",
                        level="warning",
                    )
                    clothing_check = {
                        "matches": False,
                        "discrepancies": discrepancies,
                        "retry_attempted": True,
                        "retry_generation_failed": True,
                    }
                    clothing_consistency_warning = "clothing_retry_generation_failed"
        except Exception as check_exc:
            sentry_sdk.capture_exception(check_exc)
            logger.error(f"AI clothing check skipped for set {set_id}: {check_exc}")
            clothing_consistency_warning = "clothing_check_unavailable"
            clothing_check = {"error": str(check_exc)[:200]}

        # ── Stage 2: 6 angles with 3-tier fallback ──
        # Tier 1: Self-hosted Qwen on HOSTKEY GPU
        # Tier 2: fal.ai hosted Qwen
        # Tier 3: FLUX Kontext legacy fallback
        from services.qwen_body_shots_client import QwenBodyShotsClient

        ANGLES = [
            "front", "three_quarter_left", "three_quarter_right",
            "profile_left", "profile_right", "back",
        ]

        # CANONICAL ANGLE CONVENTION — standard portrait / fashion-photography:
        #
        #   `three_quarter_left`  → subject's LEFT side of face is shown to the
        #                           camera; the subject's body is rotated toward
        #                           the camera's RIGHT, so the subject's RIGHT
        #                           shoulder/cheek is CLOSER to the camera.
        #                           (Same convention every editing tool uses —
        #                           Photoshop, Figma, Stable Diffusion, etc.)
        #   `three_quarter_right` → mirror — subject's RIGHT side of face shown,
        #                           subject's LEFT shoulder closer to camera.
        #   `profile_left`        → pure side profile, subject's LEFT side faces
        #                           the camera (camera sees subject's left side).
        #   `profile_right`       → pure side profile, subject's RIGHT side faces
        #                           the camera.
        #   `front` / `back`      → self-explanatory.
        #
        # All three engines (Qwen self-hosted, fal.ai Qwen, FLUX Kontext) and
        # the Gemini validator MUST agree on this convention. Earlier revisions
        # had three_quarter_left/right swapped relative to the standard, which
        # is why the user kept seeing the wrong shoulder forward.
        #
        # fal.ai Qwen `qwen-image-edit-2511-multiple-angles` interprets
        # horizontal_angle as the camera's orbit position around the subject.
        # In practice the LoRA's response is ASYMMETRIC: small positive values
        # (~0-50°) under-rotate to nearly frontal output, while values near
        # 360° (e.g. 315° = -45°) rotate properly. We compensate by:
        #   - using 55° (not 45°) for `three_quarter_left` to force visible
        #     rotation past the under-rotation band; subject's LEFT side of
        #     face faces camera, subject's RIGHT shoulder leads.
        #   - using 315° for `three_quarter_right`, which already rotates well;
        #     subject's RIGHT side of face faces camera, LEFT shoulder leads.
        #   - 90° / 270° for profiles, 180° for back.
        # Do NOT lower three_quarter_left below 55° (it will read frontal). If
        # 55° is still insufficient on review, escalate to 60°.
        FAL_QWEN_ANGLES = {
            "front":               {"horizontal_angle": 0,   "vertical_angle": 0},
            # fal.ai Qwen "multiple-angles" LoRA under-rotates small positive
            # horizontal_angle values (~0-50°). Empirically, 45° produces nearly
            # frontal output while 315° (= -45°) rotates correctly. We use 55°
            # here to force visible rotation. If 55° still reads too frontal,
            # escalate to 60°. Do NOT lower below 55°.
            "three_quarter_left":  {"horizontal_angle": 55,  "vertical_angle": 0},
            "three_quarter_right": {"horizontal_angle": 315, "vertical_angle": 0},
            "profile_left":        {"horizontal_angle": 90,  "vertical_angle": 0},
            "profile_right":       {"horizontal_angle": 270, "vertical_angle": 0},
            "back":                {"horizontal_angle": 180, "vertical_angle": 0},
        }

        def _build_kontext_prompt(angle: str) -> str:
            """Legacy FLUX Kontext prompt builder (Tier 3 fallback).

            Uses body_desc_for_angles which has the vision-extracted wardrobe
            (including explicit has_glasses flag) prepended to body_description.
            """
            if angle == "front":
                tmpl = get_prompt("flux_body_kontext_angle_front")
                return tmpl["system"].format(body_description=body_desc_for_angles)
            elif angle.startswith("three_quarter_"):
                # Convention: `three_quarter_left` shows the subject's LEFT side
                # of face, which means the subject's body is rotated camera-RIGHT
                # and the subject's RIGHT shoulder is closer to camera. So the
                # `{direction}` token in the prompt template — which names the
                # shoulder closer to camera — is the OPPOSITE of the angle suffix.
                shoulder_direction = "right" if angle == "three_quarter_left" else "left"
                tmpl = get_prompt("flux_body_kontext_angle_three_quarter")
                return tmpl["system"].format(body_description=body_desc_for_angles, direction=shoulder_direction)
            elif angle.startswith("profile_"):
                direction = "left" if angle == "profile_left" else "right"
                tmpl = get_prompt("flux_body_kontext_angle_profile")
                return tmpl["system"].format(body_description=body_desc_for_angles, direction=direction)
            elif angle == "back":
                tmpl = get_prompt("flux_body_kontext_angle_back")
                return tmpl["system"].format(body_description=body_desc_for_angles)
            else:
                raise ValueError(f"Unknown angle: {angle}")

        # HOSTKEY is decommissioned: skip the self-hosted Qwen tier entirely
        # when the kill-switch is set (the default) so body shots route
        # straight to fal.ai hosted Qwen (Tier 2) → FLUX Kontext (Tier 3).
        from services.hostkey_flags import hostkey_disabled, log_hostkey_skip
        qwen_client = QwenBodyShotsClient(_settings.HOSTKEY_GPU_URL)
        if hostkey_disabled():
            log_hostkey_skip("fal.ai qwen-image-edit (body shots)")
            qwen_self_hosted_ok = False
        else:
            qwen_self_hosted_ok = await qwen_client.health()
        logger.info(f"Qwen self-hosted health: {qwen_self_hosted_ok}")

        angles_dict = {}
        front_shot_key = None

        for angle in ANGLES:
            img_bytes = None
            engine_used = None

            # Tier 1: self-hosted Qwen on HOSTKEY
            if qwen_self_hosted_ok:
                try:
                    img_bytes = await qwen_client.generate(
                        reference_image_url=canonical_url,
                        angle=angle,
                        output_width=1024,
                        output_height=1792,
                        seed=locked_seed,
                    )
                    engine_used = "qwen_self_hosted"
                    logger.info(f"Tier 1 (Qwen self-hosted) succeeded for angle {angle}")
                except Exception as e:
                    logger.warning(f"Tier 1 (Qwen self-hosted) failed for angle {angle}: {e}")
                    sentry_sdk.capture_exception(e)

            # Tier 2: fal.ai hosted Qwen (uses numeric angle params, not text prompts)
            if img_bytes is None:
                try:
                    fal_angles = FAL_QWEN_ANGLES[angle]
                    fal_result = await fal_client.run_async(
                        "fal-ai/qwen-image-edit-2511-multiple-angles",
                        arguments={
                            "image_urls": [canonical_url],
                            "horizontal_angle": fal_angles["horizontal_angle"],
                            "vertical_angle": fal_angles["vertical_angle"],
                            "seed": locked_seed,
                            # Higher steps + guidance = sharper, better fabric/skin
                            # detail at the cost of ~30% extra compute per shot.
                            "num_inference_steps": 40,
                            "guidance_scale": 5.0,
                            "output_format": "jpeg",
                        },
                    )
                    async with httpx.AsyncClient() as client:
                        r = await client.get(fal_result["images"][0]["url"], timeout=60)
                        r.raise_for_status()
                        img_bytes = r.content
                    engine_used = "qwen_fal"
                    logger.info(f"Tier 2 (Qwen fal.ai) succeeded for angle {angle}")
                except Exception as e:
                    logger.warning(f"Tier 2 (Qwen fal.ai) failed for angle {angle}: {e}")
                    sentry_sdk.capture_exception(e)

            # Tier 3: FLUX Kontext legacy fallback
            if img_bytes is None:
                try:
                    prompt = _build_kontext_prompt(angle)
                    result = await fal_client.run_async(
                        "fal-ai/flux-pro/kontext",
                        arguments={
                            "prompt": prompt,
                            "image_urls": [canonical_url],
                            # Bumped from 3.5/28 → 4.0/40 for sharper detail.
                            "guidance_scale": 4.0,
                            "num_inference_steps": 40,
                            "output_format": "jpeg",
                            "seed": locked_seed,
                            "aspect_ratio": "9:16",
                            "safety_tolerance": "5",
                        },
                    )
                    images = result.get("images", [])
                    if images:
                        async with httpx.AsyncClient() as client:
                            img_resp = await client.get(images[0]["url"], timeout=60)
                            img_resp.raise_for_status()
                            img_bytes = img_resp.content
                        engine_used = "flux_kontext_legacy"
                        logger.info(f"Tier 3 (FLUX Kontext) succeeded for angle {angle}")
                except Exception as e:
                    logger.error(f"All engines failed for angle {angle}: {e}")
                    sentry_sdk.capture_exception(e)
                    continue

            if img_bytes is None:
                logger.error(f"All tiers failed for angle {angle}, skipping")
                continue

            r2_key = f"creators/{user_id}/avatar/{avatar.id}/body_shots/{set_id}/{angle}.jpg"
            await r2.upload_bytes(img_bytes, r2_key, "image/jpeg")
            angles_dict[angle] = r2_key

            if angle == "front":
                front_shot_key = r2_key

            try:
                from services.usage_tracker import calculate_fal_image_cost, log_usage
                if engine_used == "qwen_self_hosted":
                    _provider = "hostkey"
                    _cost = 0.0
                    _model = "qwen-body-shots"
                elif engine_used == "qwen_fal":
                    _provider = "fal_ai"
                    _cost = calculate_fal_image_cost("qwen", 1)
                    _model = "qwen-image-edit-2511-multiple-angles"
                else:
                    _provider = "fal_ai"
                    _cost = calculate_fal_image_cost("kontext", 1)
                    _model = "flux-pro/kontext"
                async with async_session_factory() as _usg_session:
                    await log_usage(
                        _usg_session,
                        user_id=user_id,
                        event_type="body_shot_generation",
                        provider=_provider,
                        provider_cost_usd=_cost,
                        quantity=1,
                        quantity_unit="images",
                        resource_type="avatar",
                        resource_id=avatar.id,
                        provider_model=_model,
                    )
                    await _usg_session.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)

        if not front_shot_key:
            await _mark_failed("Failed to generate front body shot")
            return

        # ── Stage 3: Gemini Vision Validation ──
        validation_results = {}
        validation_prompt_data = get_prompt("gemini_body_shot_validation")
        openrouter = get_openrouter_service()

        for angle, r2_key in angles_dict.items():
            try:
                shot_url = r2.get_public_url(r2_key)
                user_msg = f"Image URL: {shot_url}\n\nClassify the camera angle of this body shot photo."
                classified = await openrouter.generate_text(
                    prompt=user_msg,
                    system_prompt=validation_prompt_data["system"],
                    model="anthropic/claude-sonnet-4",
                    max_tokens=32,
                    temperature=0.0,
                )
                classified_angle = classified.strip().lower().replace(" ", "_")
                try:
                    from services.usage_tracker import calculate_llm_cost, log_usage
                    _u = getattr(openrouter, "last_usage", {}) or {}
                    if _u:
                        async with async_session_factory() as _usg_session:
                            await log_usage(
                                _usg_session,
                                user_id=user_id,
                                event_type="vision_check",
                                provider="openrouter",
                                provider_cost_usd=calculate_llm_cost(
                                    "anthropic/claude-sonnet-4",
                                    int(_u.get("prompt_tokens") or 0),
                                    int(_u.get("completion_tokens") or 0),
                                ),
                                quantity=int(_u.get("total_tokens") or 0),
                                quantity_unit="tokens",
                                resource_type="avatar",
                                resource_id=avatar.id,
                                provider_model="anthropic/claude-sonnet-4",
                            )
                            await _usg_session.commit()
                except Exception as _exc:
                    sentry_sdk.capture_exception(_exc)
                is_match = classified_angle == angle
                validation_results[angle] = {
                    "expected": angle,
                    "classified": classified_angle,
                    "match": is_match,
                }
                if not is_match:
                    logger.warning(
                        f"Angle validation mismatch: expected={angle}, classified={classified_angle}"
                    )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Angle validation failed for {angle}: {e}")
                validation_results[angle] = {
                    "expected": angle,
                    "classified": "unknown",
                    "match": False,
                }

        mismatches = sum(1 for v in validation_results.values() if not v["match"])
        logger.info(
            f"Body shot validation: {len(validation_results)} checked, "
            f"{mismatches} mismatches for set {set_id}"
        )

        # Update the existing BodyShotSet row created by the HTTP endpoint.
        # Mark complete with all artifacts.
        bss = await db.get(BodyShotSet, set_id)
        if bss is None:
            # Should never happen — the endpoint creates the row before scheduling
            # this task. Defensive only.
            logger.error(f"BodyShotSet {set_id} disappeared mid-pipeline")
            await _mark_failed("Internal: BodyShotSet row missing")
            return
        bss.front_shot_key = front_shot_key
        bss.angles = angles_dict
        # Persist the wardrobe-aware description that was actually fed to the
        # angle prompts, not the stale setup-time body_description, so the
        # wizard's left panel reflects what was used (e.g. suit + no glasses
        # rather than the original hoodie text).
        bss.description_used = body_desc_for_angles or body_desc
        bss.canonical_key = canonical_key
        # Merge per-angle validation with clothing-consistency result + warning flag.
        merged_validation: dict = dict(validation_results)
        if clothing_check is not None:
            merged_validation["clothing_consistency"] = clothing_check
        if clothing_consistency_warning is not None:
            merged_validation["clothing_consistency_warning"] = clothing_consistency_warning
        if face_wardrobe_extracted is not None:
            merged_validation["face_wardrobe_extracted"] = face_wardrobe_extracted
        bss.validation = merged_validation
        bss.status = "completed"
        bss.error_message = None
        await db.commit()
        logger.info(
            f"BodyShotSet {set_id} completed: {len(angles_dict)} angles, "
            f"clothing_consistency_warning={clothing_consistency_warning}"
        )

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"generate-body-shots pipeline failed for set {set_id}: {e}")
        await _mark_failed(str(e))
    finally:
        try:
            await db.close()
        except Exception:
            pass


@router.post("/ai/generate-body-shots")
async def generate_body_shots(
    req: GenerateBodyShotsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kick off the body shots pipeline as a background job.

    Returns immediately with the new set_id and status='running'. The frontend
    polls GET /api/avatar/ai/body-shot-sets/{set_id} for completion. The actual
    work runs in _run_body_shots_pipeline on the orchestrator's event loop.

    Why this is async: the pipeline takes ~60s (Stage 1 canonical generation,
    6 angle generations, 6 vision validations). A blocking HTTP request was
    being killed by Cloudflare's ~100s edge timeout.
    """
    import asyncio
    from models.avatar import BodyShotSet

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if not avatar.face_ref_key:
        raise HTTPException(status_code=400, detail="Face image required")
    if not avatar.body_description:
        raise HTTPException(status_code=400, detail="Body description required first")

    set_id = f"bss_{uuid.uuid4().hex[:12]}"
    bss = BodyShotSet(
        id=set_id,
        avatar_id=avatar.id,
        status="running",
    )
    db.add(bss)
    await db.commit()

    # Schedule background work. asyncio.create_task runs on the same event loop
    # as the request handler; once we return, the response is sent and the
    # task continues. The task opens its own DB session.
    asyncio.create_task(_run_body_shots_pipeline(set_id, avatar.id, user.id))

    return {"set_id": set_id, "status": "running"}


@router.get("/ai/body-shot-sets/{set_id}")
async def get_body_shot_set(
    set_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll endpoint for the body shots async job.

    Returns the current status and, when complete, the angle URLs for display.
    Status is one of: 'running', 'completed', 'failed'.
    """
    from services.r2_storage import get_r2_storage_service
    from models.avatar import BodyShotSet

    bss = await db.get(BodyShotSet, set_id)
    if bss is None:
        raise HTTPException(status_code=404, detail="Body shot set not found")

    avatar = await db.get(Avatar, bss.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Body shot set not found")

    r2 = get_r2_storage_service()
    payload = {
        "set_id": bss.id,
        "status": bss.status or ("completed" if bss.angles else "running"),
    }
    if bss.error_message:
        payload["error"] = bss.error_message
    if bss.angles:
        payload["angles"] = {k: r2.get_public_url(v) for k, v in bss.angles.items()}
    if bss.front_shot_key:
        payload["front_shot_url"] = r2.get_public_url(bss.front_shot_key)
    if bss.canonical_key:
        payload["canonical_url"] = r2.get_public_url(bss.canonical_key)
    if bss.validation:
        payload["validation"] = bss.validation
    if bss.description_used:
        payload["description_used"] = bss.description_used
    return payload


@router.get("/{avatar_id}/latest-body-shot-set")
async def get_latest_body_shot_set(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent BodyShotSet for an avatar, or null if none.

    The frontend uses this to decide whether to kick off a NEW body-shot
    job on mount, or just hydrate the UI from an already-completed (or
    in-flight) set. Stops the
    “navigate-away-and-come-back-regenerates-everything” footgun.
    """
    from services.r2_storage import get_r2_storage_service
    from models.avatar import BodyShotSet
    from sqlalchemy import select as _select

    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    row = (
        await db.execute(
            _select(BodyShotSet)
            .where(BodyShotSet.avatar_id == avatar_id)
            .order_by(BodyShotSet.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        return {"set": None}

    r2 = get_r2_storage_service()
    payload: dict = {
        "set_id": row.id,
        "status": row.status or ("completed" if row.angles else "running"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if row.error_message:
        payload["error"] = row.error_message
    if row.angles:
        payload["angles"] = {k: r2.get_public_url(v) for k, v in row.angles.items()}
    if row.front_shot_key:
        payload["front_shot_url"] = r2.get_public_url(row.front_shot_key)
    if row.canonical_key:
        payload["canonical_url"] = r2.get_public_url(row.canonical_key)
    if row.validation:
        payload["validation"] = row.validation
    if row.description_used:
        payload["description_used"] = row.description_used
    return {"set": payload}


class RegenerateBodyShotRequest(BaseModel):
    avatar_id: str
    set_id: str
    angle: str


@router.post("/ai/regenerate-body-shot")
async def regenerate_body_shot(
    req: RegenerateBodyShotRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate a single body shot angle using canonical front as Kontext reference (Phase D v3).

    Uses the same per-angle prompt templates as the two-stage pipeline.
    Validates the regenerated shot with LLM vision classification.
    """
    import os
    import random
    import fal_client
    import httpx
    from config import settings as _settings
    from services.r2_storage import get_r2_storage_service
    from services.ai_prompts import get_prompt
    from services.openrouter import get_openrouter_service
    from models.avatar import BodyShotSet

    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")
        if not avatar.face_ref_key or not avatar.body_description:
            raise HTTPException(status_code=400, detail="Face and body description required")

        VALID_ANGLES = {"front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"}
        if req.angle not in VALID_ANGLES:
            raise HTTPException(status_code=400, detail=f"Invalid angle: {req.angle}")

        r2 = get_r2_storage_service()
        if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
            os.environ["FAL_KEY"] = _settings.FAL_API_KEY

        body_desc = avatar.body_description

        # Find canonical front from the set to use as Kontext reference
        bss = await db.get(BodyShotSet, req.set_id)
        if not bss:
            raise HTTPException(status_code=404, detail="Body shot set not found")

        canonical_key = f"creators/{user.id}/avatar/{avatar.id}/body_shots/{req.set_id}/canonical.jpg"
        canonical_url = r2.get_public_url(canonical_key)

        # Re-use the wardrobe summary extracted by the original pipeline run if
        # present; otherwise re-run the vision extractor against the SELECTED
        # face image. This is what fixes the "body shot still wears a hoodie
        # after I edited to a suit / glasses appear when face has none" bug.
        face_wardrobe_summary: str | None = None
        try:
            stored = (bss.validation or {}).get("face_wardrobe_extracted") if isinstance(bss.validation, dict) else None
            if isinstance(stored, dict):
                summary = (stored.get("wardrobe_summary") or "").strip()
                has_glasses = bool(stored.get("has_glasses"))
                glasses_desc = (stored.get("glasses_description") or "").strip()
                if has_glasses and glasses_desc and "glasses" not in summary.lower():
                    summary = f"{summary} wearing {glasses_desc}.".strip()
                elif (not has_glasses) and ("no glasses" not in summary.lower()):
                    summary = f"{summary} No glasses, no eyewear.".strip()
                face_wardrobe_summary = summary or None
            if not face_wardrobe_summary:
                extractor_prompt_data = get_prompt("face_wardrobe_extractor")
                openrouter_for_wardrobe = get_openrouter_service()
                face_ref_public = r2.get_public_url(avatar.face_ref_key, cache_bust=True)
                raw_wardrobe = await openrouter_for_wardrobe.describe_image(
                    image_url=face_ref_public,
                    system_prompt=extractor_prompt_data["system"],
                    user_text=(
                        "Extract the wardrobe and accessories the person is wearing in this image. "
                        "Pay special attention to glasses (yes/no) and the top garment. "
                        "Respond with ONLY the JSON object."
                    ),
                    model=CREATIVE_DESCRIPTION_MODEL,
                    max_tokens=512,
                    temperature=0.0,
                )
                cleaned_w = raw_wardrobe.strip()
                if cleaned_w.startswith("```"):
                    _lines = cleaned_w.splitlines()
                    cleaned_w = (
                        "\n".join(_lines[1:-1])
                        if _lines and _lines[-1].strip() == "```"
                        else "\n".join(_lines[1:])
                    )
                import json as _json_w
                parsed = _json_w.loads(cleaned_w)
                summary = (parsed.get("wardrobe_summary") or "").strip()
                has_glasses = bool(parsed.get("has_glasses"))
                glasses_desc = (parsed.get("glasses_description") or "").strip()
                if has_glasses and glasses_desc and "glasses" not in summary.lower():
                    summary = f"{summary} wearing {glasses_desc}.".strip()
                elif (not has_glasses) and ("no glasses" not in summary.lower()):
                    summary = f"{summary} No glasses, no eyewear.".strip()
                face_wardrobe_summary = summary or None
        except Exception as wexc:
            sentry_sdk.capture_exception(wexc)
            logger.warning(f"Wardrobe extraction skipped during regenerate: {wexc}")

        # Only use the grounded vision-extracted wardrobe — never fall back to
        # concatenating body_description, which hallucinates accessories.
        body_desc_for_angles = face_wardrobe_summary or (body_desc or "")

        # Build per-angle prompt using the new templates
        if req.angle == "front":
            tmpl = get_prompt("flux_body_kontext_angle_front")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles)
        elif req.angle.startswith("three_quarter_"):
            # See _build_kontext_prompt — the {direction} token names the
            # shoulder closer to the camera, which is the OPPOSITE of the
            # angle suffix per standard portrait convention.
            shoulder_direction = "right" if req.angle == "three_quarter_left" else "left"
            tmpl = get_prompt("flux_body_kontext_angle_three_quarter")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles, direction=shoulder_direction)
        elif req.angle.startswith("profile_"):
            direction = "left" if req.angle == "profile_left" else "right"
            tmpl = get_prompt("flux_body_kontext_angle_profile")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles, direction=direction)
        elif req.angle == "back":
            tmpl = get_prompt("flux_body_kontext_angle_back")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles)

        new_seed = random.randint(1, 999999)
        result = await fal_client.run_async(
            "fal-ai/flux-pro/kontext",
            arguments={
                "prompt": prompt,
                "image_urls": [canonical_url],
                "guidance_scale": 4.0,
                "num_inference_steps": 40,
                "output_format": "jpeg",
                "seed": new_seed,
                "aspect_ratio": "9:16",
                "safety_tolerance": "5",
            },
        )
        images = result.get("images", [])
        if not images:
            raise HTTPException(status_code=500, detail="No image generated")

        async with httpx.AsyncClient() as client:
            img_resp = await client.get(images[0]["url"], timeout=60)
            img_resp.raise_for_status()
            img_bytes = img_resp.content

        r2_key = f"creators/{user.id}/avatar/{avatar.id}/body_shots/{req.set_id}/{req.angle}.jpg"
        await r2.upload_bytes(img_bytes, r2_key, "image/jpeg")

        # Update the BodyShotSet record
        if bss.angles:
            bss.angles[req.angle] = r2_key
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(bss, "angles")
            if req.angle == "front":
                bss.front_shot_key = r2_key
            await db.commit()

        # Validate the regenerated shot
        validation = None
        try:
            validation_prompt_data = get_prompt("gemini_body_shot_validation")
            openrouter = get_openrouter_service()
            shot_url = r2.get_public_url(r2_key, cache_bust=True)
            user_msg = f"Image URL: {shot_url}\n\nClassify the camera angle of this body shot photo."
            classified = await openrouter.generate_text(
                prompt=user_msg,
                system_prompt=validation_prompt_data["system"],
                model="anthropic/claude-sonnet-4",
                max_tokens=32,
                temperature=0.0,
            )
            classified_angle = classified.strip().lower().replace(" ", "_")
            validation = {
                "expected": req.angle,
                "classified": classified_angle,
                "match": classified_angle == req.angle,
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"Validation failed for regenerated {req.angle}: {e}")

        resp = {"angle": req.angle, "url": r2.get_public_url(r2_key, cache_bust=True)}
        if validation:
            resp["validation"] = validation
        return resp
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"regenerate-body-shot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/{avatar_id}/locked-voice-audio")
async def get_locked_voice_audio(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the avatar's locked_test_script synthesized with the locked voice_id.
    Cached in R2 — generate once, return cached after."""
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")
        if not avatar.voice_id:
            raise HTTPException(status_code=400, detail="Voice not locked yet")
        if not avatar.locked_test_script:
            raise HTTPException(status_code=400, detail="Test script not locked yet")

        from services.r2_storage import get_r2_storage_service
        from services.fish_audio import get_fish_audio_service

        r2 = get_r2_storage_service()
        fish = get_fish_audio_service()

        # Check for cached audio
        cache_key = f"creators/{user.id}/avatar/{avatar_id}/locked_voice.mp3"
        cached_url = r2.get_public_url(cache_key)

        # Check if cache exists by trying HEAD
        try:
            exists = await r2.key_exists(cache_key)
        except Exception:
            exists = False

        if exists:
            return {"audio_url": cached_url, "cached": True}

        # Generate TTS with Fish Audio
        tts_result = await fish.generate_tts(
            text=avatar.locked_test_script,
            voice_id=avatar.voice_id,
        )
        tmp_path = tts_result["tmp_path"]

        # Upload to R2
        import aiofiles
        async with aiofiles.open(tmp_path, "rb") as f:
            audio_bytes = await f.read()
        await r2.upload_bytes(audio_bytes, cache_key, "audio/mpeg")

        # Clean up temp file
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

        return {"audio_url": r2.get_public_url(cache_key, cache_bust=True), "cached": False}

    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"locked-voice-audio failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


class SaveTargetAudienceRequest(BaseModel):
    avatar_id: str
    target_audience: dict


@router.post("/ai/save-target-audience")
async def save_target_audience(
    req: SaveTargetAudienceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save target audience for an avatar."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        avatar.target_audience = req.target_audience
        await db.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


class SaveSetupRequest(BaseModel):
    avatar_id: str
    target_audience: dict
    name: str
    description: str
    gender: str
    body_description: Optional[str] = None
    style_preset: Optional[str] = None
    imperfections: Optional[list[str]] = None


@router.post("/ai/save-setup")
async def save_setup(
    req: SaveSetupRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save consolidated Setup page data: target_audience + name + description + gender + body_description."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        avatar.target_audience = req.target_audience
        avatar.name = req.name
        avatar.description = req.description
        avatar.gender = req.gender
        if req.body_description is not None:
            avatar.body_description = req.body_description
        if req.style_preset is not None:
            avatar.style_preset = req.style_preset
        await db.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


class RewriteAudienceDescriptionRequest(BaseModel):
    age_min: int
    age_max: int
    gender_lean: int
    gender_doesnt_matter: bool
    interests: list[str] = []
    geography: str = ""
    income_bracket: str = ""
    occupations: list[str] = []


@router.post("/ai/rewrite-audience-description")
async def rewrite_audience_description(
    req: RewriteAudienceDescriptionRequest,
    user: User = Depends(get_current_user),
):
    """Generate an audience description from all audience fields.
    Cached 60s to avoid spamming during rapid toggling."""
    try:
        import json as _json
        from services.openrouter import get_openrouter_service
        from services.ai_prompts import get_prompt

        openrouter = get_openrouter_service()
        prompt_data = get_prompt("audience_description")

        age_range = f"{req.age_min}-{req.age_max}"
        gender_desc = "Gender doesn't matter" if req.gender_doesnt_matter else f"Gender lean: {req.gender_lean} (0 = mostly female, 50 = equal, 100 = mostly male)"
        geography_desc = f"Geography: {req.geography}" if req.geography else ""
        income_desc = f"Income bracket: {req.income_bracket}" if req.income_bracket else ""
        occupations_desc = f"Occupations: {', '.join(req.occupations)}" if req.occupations else ""
        
        user_msg_parts = [
            f"Age range: {age_range}",
            gender_desc,
            f"Interests: {', '.join(req.interests) if req.interests else 'none'}",
            geography_desc,
            income_desc,
            occupations_desc
        ]
        user_msg = "\n".join([p for p in user_msg_parts if p])

        log_creative_model_use("avatar_audience_description", CREATIVE_DESCRIPTION_MODEL)
        raw = await openrouter.generate_text(
            prompt=user_msg,
            system_prompt=prompt_data["system"],
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=300,
        )
        description = raw.strip().strip('"').strip("'")

        return {"description": description}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"rewrite-audience-description failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


class RewriteAvatarNameAndDescriptionRequest(BaseModel):
    audience_description: str
    gender: str
    presets: list[str] = []
    imperfections: list[str] = []


@router.post("/ai/rewrite-avatar-name-and-description")
@router.post("/ai/rewrite-avatar-identity")
async def rewrite_avatar_identity(
    req: RewriteAvatarNameAndDescriptionRequest,
    user: User = Depends(get_current_user),
):
    """Single LLM call: returns name + description + body_description."""
    try:
        import json as _json
        from services.openrouter import get_openrouter_service
        from services.ai_prompts import get_prompt

        openrouter = get_openrouter_service()
        prompt_data = get_prompt("avatar_name_and_description")

        user_msg = (
            f"Audience description: {req.audience_description}\n"
            f"Gender: {req.gender}\n"
            f"Style presets: {', '.join(req.presets) if req.presets else 'none'}\n"
            f"Make it real details: {', '.join(req.imperfections) if req.imperfections else 'none'}"
        )

        log_creative_model_use("avatar_name_and_description", CREATIVE_DESCRIPTION_MODEL)
        raw = await openrouter.generate_text(
            prompt=user_msg,
            system_prompt=prompt_data["system"],
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=800,
        )

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            cleaned = cleaned.strip()

        # Try parsing as JSON directly, or extract JSON object from LLM preamble text
        result = None
        try:
            result = _json.loads(cleaned)
        except (_json.JSONDecodeError, TypeError):
            # LLM may wrap JSON in explanatory text — extract first { ... } block
            match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL)
            if match:
                try:
                    result = _json.loads(match.group(0))
                except (_json.JSONDecodeError, TypeError):
                    pass

        if result and isinstance(result, dict):
            return {
                "name": result.get("name", "Avatar"),
                "description": result.get("description", cleaned),
                "body_description": result.get("body_description", ""),
            }
        else:
            return {"name": "Avatar", "description": cleaned, "body_description": ""}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"rewrite-avatar-identity failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


class LockTestScriptRequest(BaseModel):
    test_script: str


@router.post("/ai/{avatar_id}/lock-test-script")
async def lock_test_script(
    avatar_id: str,
    req: LockTestScriptRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lock the test script as the single source of truth for all downstream voice playback.

    Writes the user's text to BOTH `locked_test_script` (the explicit
    user-curated value) and `test_script` (read by the InfiniteTalk preview
    pipeline). Earlier these were out of sync: the lock endpoint saved only
    to `locked_test_script`, the preview pipeline read only `test_script`,
    so the AI-auto-generated greeting (or the hardcoded fallback) leaked
    through whenever the frontend's in-memory copy of the script was empty.
    """
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        avatar.locked_test_script = req.test_script
        avatar.test_script = req.test_script
        await db.commit()
        return {"status": "locked", "locked_test_script": req.test_script}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/{avatar_id}/regenerate-preview-video")
async def regenerate_preview_video(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a real talking-head preview render using InfiniteTalk.
    Uses locked face image + locked voice audio + locked_test_script."""
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(status_code=404, detail="Avatar not found")
        if not avatar.face_ref_key:
            raise HTTPException(status_code=400, detail="Face image required")
        if not avatar.voice_id:
            raise HTTPException(status_code=400, detail="Voice not locked yet")
        if not avatar.locked_test_script:
            raise HTTPException(status_code=400, detail="Test script not locked yet")

        from services.r2_storage import get_r2_storage_service
        from services.fish_audio import get_fish_audio_service
        from services.runpod import get_runpod_service

        r2 = get_r2_storage_service()
        fish = get_fish_audio_service()
        runpod = get_runpod_service()

        avatar.progress_step = "Generating voice audio..."
        avatar.progress_percent = 10
        await db.commit()

        # Step 1: Generate TTS audio from locked script
        tts_result = await fish.generate_tts(
            text=avatar.locked_test_script,
            voice_id=avatar.voice_id,
        )
        audio_key = tts_result.get("audio_key") or f"creators/{user.id}/avatar/{avatar_id}/preview_audio.mp3"
        lipsync_audio_key = tts_result.get("lipsync_audio_key") or audio_key
        if tts_result.get("tmp_path"):
            import os
            try:
                os.unlink(tts_result["tmp_path"])
            except OSError:
                pass

        tts_duration = tts_result.get("duration_seconds", len(avatar.locked_test_script.split()) / 2.5)

        avatar.progress_step = "Rendering talking-head video..."
        avatar.progress_percent = 30
        await db.commit()

        # Step 2: Submit InfiniteTalk job
        face_url = r2.get_signed_url(avatar.face_ref_key, expires_in=7200)
        try:
            from services.lipsync_audio_prep import prepare_lipsync_audio
            raw_audio_url = r2.get_signed_url(lipsync_audio_key, expires_in=7200)
            audio_url = await prepare_lipsync_audio(
                raw_audio_url,
                render_id=f"avatar_preview_{avatar_id}",
                block_id=f"avatar_preview_{avatar_id}",
                r2=r2,
            )
        except Exception:
            audio_url = r2.get_signed_url(lipsync_audio_key, expires_in=7200)

        job_id = await runpod.submit_video_job(
            image_url=face_url,
            audio_url=audio_url,
            prompt="A person talking naturally to the camera",
            size="480p",
        )

        avatar.progress_step = "Waiting for video render..."
        avatar.progress_percent = 50
        await db.commit()

        # Step 3: Poll for completion. State-machine drives stall detection;
        # no outer timeout (per project rule "No hardcoded render timeouts").
        result = await runpod.wait_for_completion(
            job_id,
            poll_interval=5,
            audio_duration_s=tts_duration,
            quality="480p",
        )

        output = result.get("output")
        if not output:
            raise HTTPException(status_code=500, detail="AI render returned no output")

        # Step 4: Download and upload to R2
        video_url = output.get("video_url") or output.get("url") or output.get("result", {}).get("url")
        if not video_url:
            raise HTTPException(status_code=500, detail="AI render produced no video")

        import httpx
        async with httpx.AsyncClient() as client:
            vid_resp = await client.get(video_url, timeout=120)
            vid_resp.raise_for_status()
            video_bytes = vid_resp.content

        preview_key = f"creators/{user.id}/avatar/{avatar_id}/preview_video.mp4"
        await r2.upload_bytes(video_bytes, preview_key, "video/mp4")

        avatar.preview_video_key = preview_key
        avatar.test_video_key = preview_key
        avatar.progress_step = "Preview video ready"
        avatar.progress_percent = 100
        await db.commit()

        return {
            "preview_video_url": r2.get_public_url(preview_key),
            "status": "complete",
        }

    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"regenerate-preview-video failed: {e}")
        avatar.progress_step = f"Preview render failed: {str(e)[:100]}"
        avatar.progress_percent = 0
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# Phase F — Pipeline resume + render-jobs per avatar
# ═══════════════════════════════════════════════════════════════════════


@router.get("/{avatar_id}/render-jobs")
async def get_avatar_render_jobs(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return full per-step pipeline history for an avatar.

    Used by PipelineProgressView to hydrate state on page return.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.pipeline_tracker import get_pipeline_state
    state = await get_pipeline_state(db, avatar_id)

    return {
        "avatar_id": avatar_id,
        "avatar_status": avatar.status.value if hasattr(avatar.status, 'value') else avatar.status,
        "avatar_phase": avatar.active_phase.value if hasattr(avatar.active_phase, 'value') else avatar.active_phase,
        **state,
    }


@router.post("/{avatar_id}/resume-pipeline")
async def resume_pipeline(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-dispatch only the failed/stalled step. Smart resume.

    Returns {resumed_from_step, render_job_id} or {status: "already_complete"}.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.pipeline_tracker import (
        get_pipeline_state, get_pipeline_jobs, supersede_step, start_step,
        CLONE_PIPELINE_STEPS, AI_PIPELINE_STEPS,
    )
    from models.render_job import RenderJobType, RenderJobState, RenderProvider
    from services.r2_storage import get_r2_storage_service

    state = await get_pipeline_state(db, avatar_id)
    r2 = get_r2_storage_service()

    # If all complete, return early
    if state["overall"] == "complete":
        return {"status": "already_complete", "avatar_id": avatar_id}

    # If currently running, don't double-dispatch
    if state["overall"] == "running":
        raise HTTPException(status_code=409, detail="Pipeline is still running. Wait for the current step to finish.")

    # Find the first failed/stalled step
    failed_step_name = state.get("failed_step")
    if not failed_step_name:
        raise HTTPException(status_code=400, detail="No failed step found to resume.")

    # Parse job type
    try:
        failed_job_type = RenderJobType(failed_step_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown step type: {failed_step_name}")

    # Validate inputs exist for this step
    input_errors = await _validate_resume_inputs(avatar, failed_job_type, r2)
    if input_errors:
        raise HTTPException(status_code=409, detail=input_errors)

    # Supersede the old failed job
    failed_job_id = state["steps"].get(failed_step_name, {}).get("job_id")
    if failed_job_id:
        await supersede_step(db, failed_job_id)

    # Create a new job row in QUEUED state
    new_job_id = await start_step(db, avatar_id, failed_job_type, RenderProvider.HOSTKEY_LOCAL)

    # Reset avatar status to PROCESSING
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = f"Resuming: {failed_step_name}"
    await db.commit()

    # Re-dispatch the Celery task for this step
    _dispatch_step_task(avatar, user.id, failed_job_type, new_job_id)

    return {
        "resumed_from_step": failed_step_name,
        "render_job_id": new_job_id,
        "avatar_id": avatar_id,
    }


async def _validate_resume_inputs(avatar, job_type: "RenderJobType", r2) -> str | None:
    """Check that the inputs for a given step still exist. Returns error string or None."""
    from models.render_job import RenderJobType as RJT

    if job_type == RJT.CLONE_FACE_EXTRACT:
        if not avatar.video_ref_key:
            scrubber_key = f"creators/{avatar.user_id}/avatar/{avatar.id}/scrubber_video_0.mp4"
            if not await r2.key_exists(scrubber_key):
                return "Uploaded video no longer exists. Please re-upload and start from the beginning."
    elif job_type == RJT.CLONE_VOICE_EXTRACT:
        if not avatar.video_ref_key:
            scrubber_key = f"creators/{avatar.user_id}/avatar/{avatar.id}/scrubber_video_0.mp4"
            if not await r2.key_exists(scrubber_key):
                return "Uploaded video no longer exists. Please re-upload."
    elif job_type == RJT.CLONE_VOICE_TRAINING:
        if not avatar.voice_sample_key:
            return "Voice corpus not found. Need to re-run voice extraction first."
    elif job_type == RJT.CLONE_PREVIEW_RENDER:
        if not avatar.face_ref_key:
            return "Face image missing. Need to re-run face extraction."
        if not avatar.voice_id and not avatar.voice_sample_key:
            return "Voice not available. Need to re-run voice training."
    elif job_type == RJT.AI_FACE_GENERATION:
        pass  # No upstream deps
    elif job_type == RJT.AI_VOICE_GENERATION:
        pass  # No upstream deps
    elif job_type == RJT.AI_BODY_SHOTS:
        if not avatar.face_ref_key:
            return "Face image missing. Need to re-run face generation."
    elif job_type == RJT.AI_PREVIEW_RENDER:
        if not avatar.face_ref_key:
            return "Face image missing."
        if not avatar.voice_id:
            return "Voice not available."

    return None


def _dispatch_step_task(avatar, user_id: str, job_type: "RenderJobType", job_id: str):
    """Dispatch the correct Celery task for a given pipeline step."""
    from models.render_job import RenderJobType as RJT

    # Clone pipeline steps
    if job_type == RJT.CLONE_UPLOAD:
        from tasks.generate_avatar import clone_avatar_task
        clone_avatar_task.delay(avatar.id, user_id, avatar.tiktok_source_url)
    elif job_type in (RJT.CLONE_FACE_EXTRACT, RJT.CLONE_VOICE_EXTRACT):
        from tasks.generate_avatar import clone_avatar_task
        clone_avatar_task.delay(avatar.id, user_id, avatar.tiktok_source_url)
    elif job_type in (RJT.CLONE_VOICE_TRAINING, RJT.CLONE_PREVIEW_RENDER):
        from tasks.generate_avatar import generate_from_selection_task
        generate_from_selection_task.delay(avatar.id, user_id)
    # AI pipeline steps
    elif job_type in (RJT.AI_FACE_GENERATION, RJT.AI_VOICE_GENERATION, RJT.AI_BODY_SHOTS, RJT.AI_PREVIEW_RENDER):
        from tasks.generate_avatar import generate_digital_avatar_task
        generate_digital_avatar_task.delay(
            avatar.id, user_id,
            avatar.description or "",
            avatar.voice_style or "energetic",
            "energetic_beauty",
            avatar.background or "studio",
            avatar.camera_position or "waist_up",
            avatar.style or "photorealistic",
            avatar.ai_model or "meta-llama/llama-3-70b-instruct",
        )
    else:
        logger.warning(f"No task dispatch for step {job_type.value}")
