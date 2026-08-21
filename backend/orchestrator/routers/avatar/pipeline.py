"""Avatar pipeline endpoints — split from the former routers/avatar.py."""

from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
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

from ._shared import normalize_tiktok_input, _r2_key_to_url, AvatarResponse, _avatar_to_response, _BODY_MOTION_POSES, _BODY_MOTION_POSE_LABELS, _seed_body_motion_looks_from_body_shot_set

router = APIRouter()

class AvatarListResponse(BaseModel):
    avatars: list[AvatarResponse]
    total: int

@router.get("/list", response_model=AvatarListResponse)
async def list_avatars(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
):
    """List all avatars for the current user. Optional status filter (comma-separated)."""
    query = (
        select(Avatar)
        .where(Avatar.user_id == ctx.workspace_owner_id)
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

class CloneFromTikTokRequest(BaseModel):
    tiktok_url: Optional[str] = ""
    name: Optional[str] = ""
    test_script: Optional[str] = ""
    consent_confirmed: bool = False

@router.post("/clone-from-tiktok", response_model=AvatarResponse, status_code=201)
async def clone_from_tiktok(
    req: CloneFromTikTokRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    if not req.consent_confirmed:
        raise HTTPException(status_code=400, detail="Consent must be confirmed")

    # Normalize TikTok input: @handle, bare handle, partial URL → full URL
    tiktok_url = normalize_tiktok_input(req.tiktok_url)
    if not tiktok_url or "tiktok.com/@" not in tiktok_url:
        raise HTTPException(status_code=400, detail="Please enter a valid TikTok username or profile URL.")

    from services import billing_service
    await billing_service.check_avatar_slot_available(db, ctx.workspace_owner_id)

    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=ctx.workspace_owner_id,        type=AvatarType.CLONE,
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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

    from services import billing_service
    await billing_service.check_avatar_slot_available(db, ctx.workspace_owner_id)

    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=ctx.workspace_owner_id,        type=AvatarType.CLONE,
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Create a clone avatar using uploaded photo and/or audio (camera, mic, or file upload)."""
    if not consent_confirmed:
        raise HTTPException(status_code=400, detail="Consent must be confirmed")

    from services import billing_service
    await billing_service.check_avatar_slot_available(db, ctx.workspace_owner_id)

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
        face_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/face_ref.jpg"
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
        voice_sample_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/voice_sample.{ext}"
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
        user_id=ctx.workspace_owner_id,        type=AvatarType.CLONE,
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

class GenerateDigitalRequest(BaseModel):
    name: Optional[str] = ""
    description: Optional[str] = ""
    voice_style: Optional[str] = "energetic"
    background: Optional[str] = "studio"
    camera_position: Optional[str] = "waist_up"
    style: Optional[str] = "photorealistic"
    ai_model: Optional[str] = "meta-llama/llama-3-70b-instruct"
    persona_preset: Optional[str] = "energetic_beauty"

@router.post("/generate-digital", response_model=AvatarResponse, status_code=201)
async def generate_digital(
    req: GenerateDigitalRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    from services import billing_service
    await billing_service.check_avatar_slot_available(db, ctx.workspace_owner_id)

    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=ctx.workspace_owner_id,        type=AvatarType.DIGITAL,
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
        avatar_id, ctx.workspace_owner_id,
        req.description or "", req.voice_style or "energetic",
        req.persona_preset or "energetic_beauty",
        req.background or "studio", req.camera_position or "waist_up",
        req.style or "photorealistic", req.ai_model or "meta-llama/llama-3-70b-instruct",
    )

    return _avatar_to_response(avatar)

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

@router.get("/status/{avatar_id}", response_model=AvatarResponse)
async def get_avatar_status(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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

@router.post("/{avatar_id}/approve", response_model=AvatarResponse)
async def approve_avatar(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Approve the avatar after reviewing the test video. Makes it available for Casts."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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
            await db.flush()  # need original_look.id before create_base_scenes
            from services.mic_on_look import create_base_scenes
            from models.avatar_look import DEFAULT_ENVIRONMENT
            # No environment choice exists yet at avatar-approval time (that's
            # only collected later, per-scene, in AddLookDialog) — studio is
            # the sensible bootstrap default for the auto-created "Original"
            # look's two base scenes (mic-visible / mic-off).
            await create_base_scenes(
                avatar_id,
                original_look.id,
                DEFAULT_ENVIRONMENT,
                db,
            )

    # Idempotent safety net — the primary seeding now happens right when
    # BodyShotSet generation completes (see _run_body_shots_pipeline), not
    # only here. Kept here too in case an avatar's BodyShotSet predates
    # that fix, or was completed through some other path.
    seeded_count = await _seed_body_motion_looks_from_body_shot_set(db, avatar_id)

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

class RegenerateRequest(BaseModel):
    test_script: Optional[str] = None  # Custom text for the avatar to speak

@router.post("/{avatar_id}/regenerate", response_model=AvatarResponse)
async def regenerate_avatar(
    avatar_id: str,
    req: RegenerateRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate the avatar's test video with optional new test script."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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
        avatar_id, ctx.workspace_owner_id,
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Re-run only the voice cloning pipeline for an avatar.

    Used when the initial voice clone produced a bad result (e.g. GPU was busy,
    BS-RoFormer fell back to CPU and timed out, resulting in wrong gender voice).
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if avatar.status not in (AvatarStatus.READY, AvatarStatus.FACE_CANDIDATES_READY, AvatarStatus.APPROVED):
        raise HTTPException(status_code=400, detail="Avatar must be in ready or approved status to re-clone voice")

    if not avatar.video_ref_key and not avatar.voice_sample_key:
        raise HTTPException(status_code=400, detail="No voice reference found for this avatar. Cannot re-clone voice.")

    if avatar.video_ref_key:
        from tasks.generate_avatar import process_voice_pipeline_task
        process_voice_pipeline_task.delay(avatar_id, ctx.workspace_owner_id, avatar.video_ref_key, segment_start, segment_end)
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
    # segment_start = (avatar.persona_profile or {}).get("segment_start", 0)
    # segment_end = (avatar.persona_profile or {}).get("segment_end", 60)

    # avatar.voice_clone_progress = 0
    # avatar.progress_step = "Re-cloning voice..."
    # await db.commit()

    # from tasks.generate_avatar import process_voice_pipeline_task
    # process_voice_pipeline_task.delay(
    #     avatar_id, ctx.workspace_owner_id,
    #     avatar.video_ref_key, segment_start, segment_end,
    # )

    return {"status": "ok", "message": "Voice re-clone started"}

@router.get("/{avatar_id}/video")
async def stream_avatar_video(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Stream the avatar test video directly. Supports Range requests for smooth playback."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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

class CandidatesResponse(BaseModel):
    avatar_id: str
    status: str
    candidate_frames: list[str]

@router.get("/{avatar_id}/candidates", response_model=CandidatesResponse)
async def get_candidates(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return candidate frame URLs for an avatar in candidates_ready status."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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

async def _claim_avatar_for_generation(
    avatar: Avatar,
    db: AsyncSession,
    *,
    require_status: Optional[AvatarStatus] = None,
    stale_after_seconds: Optional[int] = None,
) -> bool:
    """Atomically transition avatar.status -> PROCESSING. Returns True iff
    this call won the transition.

    Two endpoints that both dispatch avatar generation (e.g. select-frame
    fired twice — a double-click, a frontend retry, two tabs) used to do a
    plain read-then-write: check ``avatar.status``, then set it to
    PROCESSING and commit. That has a race window — a second near-
    simultaneous request can read the pre-transition status before the
    first one's commit lands, so both pass the check and both dispatch a
    full (expensive) generation task for the same avatar. That's exactly
    what happened: two Celery tasks, two RunPod jobs, double GPU/API cost,
    and both writing their finished video to the same R2 key so one
    silently overwrote the other.

    A single conditional UPDATE closes the gap: only one concurrent
    caller's WHERE clause can still match by the time it executes, so only
    one caller's statement affects a row. ``require_status`` pins the
    precondition (e.g. CANDIDATES_READY) when the endpoint needs one;
    omitted, it just requires "not already PROCESSING".

    ``stale_after_seconds`` matters for endpoints where PROCESSING is NOT
    exclusive to this operation — the AI-avatar wizard reuses the same
    ``avatar.status`` across setup/face/voice/shots/preview, so an avatar
    can legitimately sit at PROCESSING for minutes from an earlier, already-
    finished step by the time the user reaches this one. Without a
    staleness window, that leftover value would permanently 409 every
    future call — a real bug this shipped with initially: an avatar stuck
    at PROCESSING from a step 9 minutes earlier blocked Preview generation
    forever. When set, a PROCESSING row still claims successfully if
    ``updated_at`` is older than the window — only a *recent* PROCESSING
    (an actual concurrent in-flight request) blocks the claim.
    """
    conditions = [Avatar.id == avatar.id]
    if require_status is not None:
        conditions.append(Avatar.status == require_status)
    elif stale_after_seconds is not None:
        stale_cutoff = datetime.utcnow() - timedelta(seconds=stale_after_seconds)
        conditions.append(or_(
            Avatar.status != AvatarStatus.PROCESSING,
            Avatar.updated_at < stale_cutoff,
        ))
    else:
        conditions.append(Avatar.status != AvatarStatus.PROCESSING)

    result = await db.execute(
        sa_update(Avatar).where(*conditions).values(status=AvatarStatus.PROCESSING)
    )
    await db.commit()
    if result.rowcount > 0:
        avatar.status = AvatarStatus.PROCESSING
        return True
    return False

class SelectFrameRequest(BaseModel):
    frame_url: str

@router.post("/{avatar_id}/select-frame", response_model=AvatarResponse)
async def select_frame(
    avatar_id: str,
    req: SelectFrameRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Select a candidate frame URL and launch the remaining pipeline."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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

    if not await _claim_avatar_for_generation(avatar, db, require_status=AvatarStatus.CANDIDATES_READY):
        raise HTTPException(
            status_code=409,
            detail="This avatar is already generating. Please wait for it to finish before trying again.",
        )

    avatar.face_ref_key = face_ref_key
    avatar.progress_step = "Frame selected — continuing pipeline..."
    avatar.progress_percent = 40
    await db.commit()

    from tasks.generate_avatar import generate_from_selection_task
    generate_from_selection_task.delay(avatar_id, ctx.workspace_owner_id)
    return _avatar_to_response(avatar)

@router.post("/{avatar_id}/capture-frame", response_model=AvatarResponse)
async def capture_frame(
    avatar_id: str,
    frame: UploadFile = File(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a captured frame from the video scrubber and launch the remaining pipeline."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.status != AvatarStatus.CANDIDATES_READY:
        raise HTTPException(status_code=400, detail="Avatar is not awaiting frame capture")

    frame_bytes = await frame.read()
    if len(frame_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Captured frame is too small.")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    face_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/face_ref.jpg"
    await r2.upload_bytes(frame_bytes, face_key, "image/jpeg")

    if not await _claim_avatar_for_generation(avatar, db, require_status=AvatarStatus.CANDIDATES_READY):
        raise HTTPException(
            status_code=409,
            detail="This avatar is already generating. Please wait for it to finish before trying again.",
        )

    avatar.face_ref_key = face_key
    avatar.progress_step = "Frame captured — cloning voice and generating video..."
    avatar.progress_percent = 40
    await db.commit()

    from tasks.generate_avatar import generate_from_selection_task
    generate_from_selection_task.delay(avatar_id, ctx.workspace_owner_id)
    return _avatar_to_response(avatar)

@router.delete("/{avatar_id}", status_code=204)
async def delete_avatar(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if avatar.id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default avatar")

    # Any cast referencing this avatar — regardless of status — blocks the
    # delete at the DB level anyway (Cast.avatar_id has no ondelete rule),
    # so check for that directly instead of guessing which CastStatus
    # values count as "in use". A hardcoded status whitelist here would be
    # exactly the kind of check that goes stale as new statuses get added
    # (which is what happened to the equivalent product-delete guard).
    from models.cast import Cast
    in_use = (
        await db.execute(select(Cast.id).where(Cast.avatar_id == avatar_id).limit(1))
    ).scalar_one_or_none()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="This avatar is used by one or more casts. Delete or reassign those casts first.",
        )

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
    try:
        await db.commit()
    except IntegrityError as e:
        # Safety net in case the check above missed a referencing row (e.g.
        # a table added later that also FKs to avatars) — never let a raw
        # DB constraint error surface as an unhandled 500.
        await db.rollback()
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=409,
            detail="This avatar is still referenced by other data and can't be deleted.",
        )

@router.put("/{avatar_id}")
@router.patch("/{avatar_id}")
async def update_avatar(
    avatar_id: str,
    update: dict = Body(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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
