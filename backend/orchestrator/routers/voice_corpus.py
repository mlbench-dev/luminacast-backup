"""Voice Corpus — upload, list, delete voice corpus entries for an avatar."""
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.avatar import Avatar
from models.user import User
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/avatar", tags=["voice-corpus"])

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".mp3", ".wav", ".m4a", ".aac"}
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB


def _entry_to_dict(entry: VoiceCorpusEntry) -> dict:
    r2 = get_r2_storage_service()
    return {
        "id": entry.id,
        "avatar_id": entry.avatar_id,
        "source_type": entry.source_type,
        "source_url": entry.source_url,
        "audio_r2_key": entry.audio_r2_key,
        "audio_url": r2.get_public_url(entry.audio_r2_key) if entry.audio_r2_key else None,
        "transcript": entry.transcript,
        "duration_seconds": entry.duration_seconds,
        "status": entry.status,
        "error_message": entry.error_message,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.post("/{avatar_id}/voice-corpus/upload")
async def upload_voice_corpus(
    avatar_id: str,
    file: Optional[UploadFile] = File(None),
    source_url: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a video/audio file or provide a URL for voice corpus extraction."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    if not file and not source_url:
        raise HTTPException(400, "Either file or source_url must be provided")

    r2 = get_r2_storage_service()
    entry_id = f"vc_{uuid.uuid4().hex[:16]}"
    audio_r2_key = None
    source_type = "uploaded"

    if file:
        original_name = file.filename or "video.mp4"
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise HTTPException(400, f"File type {ext} not allowed. Use: {allowed}")

        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024 * 1024)} MB.")

        # Upload raw file to R2 (processor will handle conversion)
        audio_r2_key = f"creators/{user.id}/avatars/{avatar_id}/voice-corpus/{entry_id}{ext}"
        content_type_map = {
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
            ".m4v": "video/x-m4v", ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".m4a": "audio/mp4", ".aac": "audio/aac",
        }
        await r2.upload_bytes(data, audio_r2_key, content_type=content_type_map.get(ext, "application/octet-stream"))
        source_type = "uploaded"

    elif source_url:
        source_type = "tiktok_scrape"

    entry = VoiceCorpusEntry(
        id=entry_id,
        avatar_id=avatar_id,
        source_type=source_type,
        source_url=source_url if source_url else None,
        audio_r2_key=audio_r2_key,
        status="pending",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    # Dispatch Celery task
    from tasks.voice_corpus import process_voice_corpus_entry
    process_voice_corpus_entry.delay(entry_id)

    return _entry_to_dict(entry)


@router.get("/{avatar_id}/voice-corpus")
async def list_voice_corpus(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all voice corpus entries for an avatar with total ready duration."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    result = await db.execute(
        select(VoiceCorpusEntry)
        .where(VoiceCorpusEntry.avatar_id == avatar_id)
        .order_by(VoiceCorpusEntry.created_at.desc())
    )
    entries = result.scalars().all()

    total_duration = sum(
        (e.duration_seconds or 0.0) for e in entries if e.status == "ready"
    )

    return {
        "entries": [_entry_to_dict(e) for e in entries],
        "total_ready_duration_seconds": total_duration,
        "total": len(entries),
    }


@router.delete("/{avatar_id}/voice-corpus/{entry_id}")
async def delete_voice_corpus_entry(
    avatar_id: str,
    entry_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a voice corpus entry and its R2 audio (best-effort)."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    entry = await db.get(VoiceCorpusEntry, entry_id)
    if not entry or entry.avatar_id != avatar_id:
        raise HTTPException(404, "Voice corpus entry not found")

    # Best-effort R2 cleanup
    if entry.audio_r2_key:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
            from config import settings
            s3 = boto3.client(
                "s3",
                endpoint_url=settings.R2_ENDPOINT,
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                config=BotoConfig(signature_version="s3v4"),
                region_name="auto",
            )
            s3.delete_object(Bucket=settings.R2_BUCKET, Key=entry.audio_r2_key)
        except Exception as e:
            logger.warning("Failed to delete R2 audio for %s: %s", entry_id, e)

    await db.delete(entry)
    await db.commit()
    return {"deleted": True, "id": entry_id}
