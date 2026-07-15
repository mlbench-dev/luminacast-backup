"""User Photo Library — upload, list, delete personal photo assets."""

import logging
import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import Optional

import sentry_sdk
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.user_photo import UserPhotoAsset
from routers.auth import get_current_user
from services import audit_log
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user-photos", tags=["user-photos"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
CONTENT_TYPE_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _probe_image(data: bytes) -> dict:
    """Read image dimensions via PIL. Returns {} on failure (non-fatal)."""
    try:
        from PIL import Image

        img = Image.open(BytesIO(data))
        return {"width": img.width, "height": img.height}
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return {}


@router.get("")
async def list_user_photos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's photo assets (not deleted), with public URLs."""
    result = await db.execute(
        select(UserPhotoAsset)
        .where(UserPhotoAsset.user_id == user.id, UserPhotoAsset.deleted_at.is_(None))
        .order_by(UserPhotoAsset.created_at.desc())
    )
    photos = result.scalars().all()
    r2 = get_r2_storage_service()
    return {
        "photos": [
            {
                "id": p.id,
                "name": p.name,
                "url": r2.get_public_url(p.r2_key),
                "thumbnail": r2.get_public_url(p.r2_key),
                "r2_key": p.r2_key,
                "width": p.width,
                "height": p.height,
                "file_size_bytes": p.file_size_bytes,
                "original_filename": p.original_filename,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in photos
        ],
        "total": len(photos),
    }


@router.post("/upload")
async def upload_user_photo(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload an image file, probe dimensions, store in R2."""
    original_name = file.filename or "photo.jpg"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(400, f"File type {ext} not allowed. Use: {allowed}")

    content_type = file.content_type or CONTENT_TYPE_BY_EXT.get(ext, "image/jpeg")
    if content_type not in ALLOWED_CONTENT_TYPES:
        # Tolerate browsers that send unusual mime types; fall back by extension
        content_type = CONTENT_TYPE_BY_EXT.get(ext, "image/jpeg")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024 * 1024)} MB.")

    probe = _probe_image(data)

    photo_id = f"up_{uuid.uuid4().hex[:16]}"
    r2_key = f"user-photos/{user.id}/{photo_id}{ext}"

    try:
        r2 = get_r2_storage_service()
        await r2.upload_bytes(data, r2_key, content_type=content_type)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise

    asset = UserPhotoAsset(
        id=photo_id,
        user_id=user.id,
        name=name or os.path.splitext(original_name)[0],
        r2_key=r2_key,
        width=probe.get("width"),
        height=probe.get("height"),
        file_size_bytes=len(data),
        content_type=content_type,
        original_filename=original_name,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    try:
        await audit_log.record(
            db, user_id=user.id, action="media.photo_upload", entity_type="media",
            entity_id=asset.id, after={"name": asset.name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "id": asset.id,
        "name": asset.name,
        "url": r2.get_public_url(r2_key),
        "thumbnail": r2.get_public_url(r2_key),
        "r2_key": r2_key,
        "width": asset.width,
        "height": asset.height,
        "file_size_bytes": asset.file_size_bytes,
        "original_filename": asset.original_filename,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


@router.delete("/{photo_id}")
async def delete_user_photo(
    photo_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a photo asset."""
    result = await db.execute(
        select(UserPhotoAsset).where(
            UserPhotoAsset.id == photo_id,
            UserPhotoAsset.user_id == user.id,
            UserPhotoAsset.deleted_at.is_(None),
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Photo not found")

    asset.deleted_at = datetime.utcnow()
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="media.photo_delete", entity_type="media",
            entity_id=photo_id, before={"name": asset.name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"status": "deleted", "id": photo_id}
