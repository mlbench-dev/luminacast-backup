"""Avatar ai_voice endpoints — split from the former routers/avatar.py."""

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

from ._shared import normalize_tiktok_input, _r2_key_to_url, AvatarResponse, _avatar_to_response, _BODY_MOTION_POSES, _BODY_MOTION_POSE_LABELS, _seed_body_motion_looks_from_body_shot_set

router = APIRouter()

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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        preview_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/voice_preview.mp3"
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Set the selected voice ID on the avatar."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate TTS + InfiniteTalk test video for AI avatar."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if not avatar.face_ref_key:
        raise HTTPException(status_code=400, detail="No face selected")
    if not avatar.voice_id:
        raise HTTPException(status_code=400, detail="No voice selected")

    # NOT using _claim_avatar_for_generation here: this wizard reuses
    # avatar.status across every unrelated step (setup/face/voice/shots/
    # preview all write PROCESSING), so the value carries no information
    # about whether a preview generation specifically is in flight. A time-
    # based staleness heuristic was tried and failed — a user moving briskly
    # through the wizard reaches this endpoint well within any reasonable
    # "recent duplicate" window, causing false 409s on legitimate first
    # attempts. Fixing the underlying double-submission race for this
    # endpoint needs a dedicated per-operation signal (e.g. a RenderJob row
    # or a new column), not a reuse of the shared status field — left as a
    # known gap rather than another guess.
    if req.test_script:
        avatar.test_script = req.test_script
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = "Generating preview..."
    avatar.progress_percent = 10
    await db.commit()

    # Launch the generate_from_selection_task which handles TTS + InfiniteTalk
    from tasks.generate_avatar import generate_from_selection_task
    generate_from_selection_task.delay(avatar_id, ctx.workspace_owner_id)

    return {"status": "ok", "message": "Preview generation started"}

async def _clone_voice_for_avatar(
    avatar: Avatar,
    user: User,
    workspace_owner_id: str,
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

    voice_sample_key = f"creators/{workspace_owner_id}/avatar/{avatar.id}/voice_sample.{ext}"
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Clone a voice from an uploaded audio sample for AI avatar."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    audio_bytes = await file.read()
    if len(audio_bytes) < 5000:
        raise HTTPException(status_code=400, detail="Audio file too small — need at least 30 seconds of speech")
    if len(audio_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio file too large — max 50 MB")

    ext = (file.filename or "audio.mp3").rsplit(".", 1)[-1].lower()
    return await _clone_voice_for_avatar(
        avatar, user, ctx.workspace_owner_id, audio_bytes, ext, file.content_type or "audio/mpeg", db,
    )

MIN_VOICE_TRAIN_SECONDS = 8.0

class CloneVoiceFromCorpusRequest(BaseModel):
    # Accept a single id or a list — multiple ready samples are concatenated
    # into one training file so the user can pick several recordings.
    corpus_entry_id: Optional[str] = None
    corpus_entry_ids: Optional[list[str]] = None

@router.post("/ai/{avatar_id}/clone-voice-from-corpus")
async def ai_clone_voice_from_corpus(
    avatar_id: str,
    req: CloneVoiceFromCorpusRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
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
        avatar, user, ctx.workspace_owner_id, audio_bytes, "wav", "audio/wav", db,
    )

@router.get("/{avatar_id}/locked-voice-audio")
async def get_locked_voice_audio(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return the avatar's locked_test_script synthesized with the locked voice_id.
    Cached in R2 — generate once, return cached after."""
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
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
        cache_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/locked_voice.mp3"
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
