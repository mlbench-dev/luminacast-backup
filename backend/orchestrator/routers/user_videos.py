"""User Video Library — upload, list, delete personal video assets."""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.user import User
from models.user_video import UserVideoAsset
from routers.auth import get_current_user
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.video_thumbnail import extract_video_thumbnail_jpeg
import sentry_sdk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user-videos", tags=["user-videos"])

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}


def _probe_video(path: str) -> dict:
    """Run ffprobe and return duration, width, height."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed: {result.stderr[:200]}")
    data = json.loads(result.stdout)

    duration = None
    width = None
    height = None

    # Try streams first for video dimensions
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width", 0)) or None
            height = int(stream.get("height", 0)) or None
            if stream.get("duration"):
                duration = float(stream["duration"])
            break

    # Fallback duration from format
    if duration is None and data.get("format", {}).get("duration"):
        duration = float(data["format"]["duration"])

    return {"duration": duration, "width": width, "height": height}


@router.get("")
async def list_user_videos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's video assets (not deleted), with public URLs."""
    result = await db.execute(
        select(UserVideoAsset)
        .where(UserVideoAsset.user_id == user.id, UserVideoAsset.deleted_at.is_(None))
        .order_by(UserVideoAsset.created_at.desc())
    )
    videos = result.scalars().all()
    r2 = get_r2_storage_service()
    return {
        "videos": [
            {
                "id": v.id,
                "name": v.name,
                "url": r2.get_public_url(v.r2_key),
                "r2_key": v.r2_key,
                "thumbnail": r2.get_public_url(v.thumbnail_r2_key) if v.thumbnail_r2_key else None,
                "duration_seconds": v.duration_seconds,
                "width": v.width,
                "height": v.height,
                "file_size_bytes": v.file_size_bytes,
                "original_filename": v.original_filename,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ],
        "total": len(videos),
    }


@router.post("/upload")
async def upload_user_video(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a video file, probe metadata, store in R2."""
    # Validate extension
    original_name = file.filename or "video.mp4"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ', '.join(ALLOWED_EXTENSIONS)
        raise HTTPException(400, f'File type {ext} not allowed. Use: {allowed}')

    # Read and validate size
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024*1024)} MB.")

    # Save to temp file for ffprobe
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        # Probe video metadata
        loop = asyncio.get_event_loop()
        probe = await loop.run_in_executor(None, _probe_video, tmp_path)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        os.unlink(tmp_path)
        raise HTTPException(400, f"Invalid video file: {str(e)[:200]}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Upload to R2
    video_id = f"uv_{uuid.uuid4().hex[:16]}"
    r2_key = f"user-videos/{user.id}/{video_id}{ext}"

    r2 = get_r2_storage_service()
    content_type_map = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".m4v": "video/x-m4v"}
    await r2.upload_bytes(data, r2_key, content_type=content_type_map.get(ext, "video/mp4"))

    # Best-effort thumbnail extraction — failure here never blocks upload.
    thumbnail_r2_key: Optional[str] = None
    try:
        thumb_bytes = await extract_video_thumbnail_jpeg(data, suffix=ext)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("user video thumbnail extraction raised: %s", e)
        thumb_bytes = b""
    if thumb_bytes:
        thumb_key = f"user-videos/{user.id}/{video_id}_thumb.jpg"
        try:
            await r2.upload_bytes(thumb_bytes, thumb_key, content_type="image/jpeg")
            thumbnail_r2_key = thumb_key
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("user video thumbnail upload failed: %s", e)

    # Save to DB
    asset = UserVideoAsset(
        id=video_id,
        user_id=user.id,
        name=name or os.path.splitext(original_name)[0],
        r2_key=r2_key,
        thumbnail_r2_key=thumbnail_r2_key,
        duration_seconds=probe.get("duration"),
        width=probe.get("width"),
        height=probe.get("height"),
        file_size_bytes=len(data),
        original_filename=original_name,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    try:
        await audit_log.record(
            db, user_id=user.id, action="media.video_upload", entity_type="media",
            entity_id=video_id, after={"name": asset.name, "duration_seconds": asset.duration_seconds},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "id": asset.id,
        "name": asset.name,
        "url": r2.get_public_url(r2_key),
        "r2_key": r2_key,
        "thumbnail": r2.get_public_url(asset.thumbnail_r2_key) if asset.thumbnail_r2_key else None,
        "duration_seconds": asset.duration_seconds,
        "width": asset.width,
        "height": asset.height,
        "file_size_bytes": asset.file_size_bytes,
        "original_filename": asset.original_filename,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


@router.delete("/{video_id}")
async def delete_user_video(
    video_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a video asset."""
    result = await db.execute(
        select(UserVideoAsset).where(
            UserVideoAsset.id == video_id,
            UserVideoAsset.user_id == user.id,
            UserVideoAsset.deleted_at.is_(None),
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Video not found")

    asset.deleted_at = datetime.utcnow()
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="media.video_delete", entity_type="media",
            entity_id=video_id, before={"name": asset.name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"status": "deleted", "id": video_id}
