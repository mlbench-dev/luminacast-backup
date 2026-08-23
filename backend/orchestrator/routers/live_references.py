"""Live References — upload a past live session, transcribe it, and distil a
Live Style Assessment used to condition the script prompt.

A reference is scoped to EXACTLY ONE of avatar_id / cast_id (the caller must
own the target). Upload mirrors the voice-corpus multipart pattern: the raw
file lands in R2, a row is created with status=uploaded, and a Celery chain
(transcribe -> assess) runs in the background.
"""
import logging
import os
import uuid
from typing import Optional

import sentry_sdk
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.avatar import Avatar
from models.cast import Cast
from models.live_reference import LiveReference
from models.user import User
from routers.auth import get_current_user
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live-references", tags=["live-references"])

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4a", ".mp3", ".wav"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
_CONTENT_TYPE_MAP = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
}


def _max_bytes() -> int:
    return int(os.environ.get("LIVE_REF_MAX_MB", "1024")) * 1024 * 1024


def _to_dict(ref: LiveReference) -> dict:
    r2 = get_r2_storage_service()
    return {
        "id": ref.id,
        "avatar_id": ref.avatar_id,
        "cast_id": ref.cast_id,
        "user_id": ref.user_id,
        "source_r2_key": ref.source_r2_key,
        "source_url": r2.get_public_url(ref.source_r2_key) if ref.source_r2_key else None,
        "media_kind": ref.media_kind,
        "duration_seconds": ref.duration_seconds,
        "transcript_text": ref.transcript_text,
        "transcript_segments": ref.transcript_segments,
        "assessment": ref.assessment,
        "status": ref.status,
        "error_message": ref.error_message,
        "is_active": ref.is_active,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
    }


async def _assert_scope_owned(
    db: AsyncSession, user: User, avatar_id: Optional[str], cast_id: Optional[str]
) -> None:
    """Exactly one of avatar_id / cast_id must be set and owned by the user."""
    if bool(avatar_id) == bool(cast_id):
        raise HTTPException(400, "Provide exactly one of avatar_id or cast_id")
    if avatar_id:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(404, "Avatar not found")
    else:
        cast = await db.get(Cast, cast_id)
        if not cast or cast.user_id != user.id:
            raise HTTPException(404, "Cast not found")


@router.post("")
async def create_live_reference(
    file: Optional[UploadFile] = File(None),
    source_r2_key: Optional[str] = Form(None),
    avatar_id: Optional[str] = Form(None),
    cast_id: Optional[str] = Form(None),
    media_kind: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a live reference from an uploaded file OR a pre-uploaded R2 key."""
    await _assert_scope_owned(db, user, avatar_id, cast_id)

    if not file and not source_r2_key:
        raise HTTPException(400, "Either file or source_r2_key must be provided")

    ref_id = f"lref_{uuid.uuid4().hex[:16]}"
    r2 = get_r2_storage_service()
    resolved_key = source_r2_key
    resolved_kind = (media_kind or "").strip().lower() or None

    if file:
        original_name = file.filename or "live.mp4"
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise HTTPException(400, f"File type {ext} not allowed. Use: {allowed}")

        data = await file.read()
        if len(data) > _max_bytes():
            raise HTTPException(400, f"File too large. Max {_max_bytes() // (1024 * 1024)} MB.")

        scope_seg = f"avatars/{avatar_id}" if avatar_id else f"casts/{cast_id}"
        resolved_key = f"creators/{user.id}/{scope_seg}/live-references/{ref_id}{ext}"
        await r2.upload_bytes(
            data, resolved_key,
            content_type=_CONTENT_TYPE_MAP.get(ext, "application/octet-stream"),
        )
        resolved_kind = resolved_kind or ("video" if ext in VIDEO_EXTENSIONS else "audio")
    else:
        ext = os.path.splitext(source_r2_key)[1].lower()
        resolved_kind = resolved_kind or ("video" if ext in VIDEO_EXTENSIONS else "audio")

    ref = LiveReference(
        id=ref_id,
        avatar_id=avatar_id,
        cast_id=cast_id,
        user_id=user.id,
        source_r2_key=resolved_key,
        media_kind=resolved_kind or "video",
        status="uploaded",
    )
    db.add(ref)
    await db.commit()
    await db.refresh(ref)

    from tasks.live_reference import transcribe
    transcribe.delay(ref_id)

    return {"live_reference_id": ref.id, "status": ref.status}


@router.get("/{live_reference_id}")
async def get_live_reference(
    live_reference_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ref = await db.get(LiveReference, live_reference_id)
    if not ref or ref.user_id != user.id:
        raise HTTPException(404, "Live reference not found")
    return _to_dict(ref)


@router.delete("/{live_reference_id}")
async def delete_live_reference(
    live_reference_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an uploaded live reference — the row and its stored recording.

    Exemplars derived from it (live_reference_exemplars) cascade-delete at
    the DB level via their ON DELETE CASCADE foreign key.
    """
    ref = await db.get(LiveReference, live_reference_id)
    if not ref or ref.user_id != user.id:
        raise HTTPException(404, "Live reference not found")

    if ref.source_r2_key:
        r2 = get_r2_storage_service()
        await r2.delete_object(ref.source_r2_key)

    await db.delete(ref)
    await db.commit()
    return {"ok": True}


@router.post("/{live_reference_id}/activate")
async def activate_live_reference(
    live_reference_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Switch which past-live style is used for future scripts in this
    scope, without re-uploading and re-paying for transcription/assessment.

    Only an already-assessed row can be activated. Deactivates any other
    active row in the same scope (avatar_id or cast_id) first — the two
    partial unique indexes from the migration also enforce this at the DB
    level as a safety net.
    """
    ref = await db.get(LiveReference, live_reference_id)
    if not ref or ref.user_id != user.id:
        raise HTTPException(404, "Live reference not found")
    if ref.status != "assessed":
        raise HTTPException(400, "Only a fully-processed recording can be activated")

    scope_col = LiveReference.avatar_id if ref.avatar_id else LiveReference.cast_id
    scope_val = ref.avatar_id or ref.cast_id
    await db.execute(
        update(LiveReference)
        .where(scope_col == scope_val, LiveReference.id != ref.id)
        .values(is_active=False)
    )
    ref.is_active = True
    await db.commit()
    return {"ok": True}


@router.get("")
async def list_live_references(
    avatar_id: Optional[str] = None,
    cast_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LiveReference).where(LiveReference.user_id == user.id)
    if avatar_id:
        stmt = stmt.where(LiveReference.avatar_id == avatar_id)
    if cast_id:
        stmt = stmt.where(LiveReference.cast_id == cast_id)
    stmt = stmt.order_by(LiveReference.created_at.desc())
    try:
        result = await db.execute(stmt)
        refs = result.scalars().all()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise
    return {"live_references": [_to_dict(r) for r in refs], "total": len(refs)}
