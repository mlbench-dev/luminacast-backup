"""Avatar background library — upload, AI-generate, list, rename, delete."""

import logging
import os
import uuid
from typing import Optional

import httpx
import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.avatar import Avatar
from models.avatar_background import AvatarBackground, BackgroundSource
from models.user import User
from routers.auth import get_current_user
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/avatars", tags=["avatar-backgrounds"])


class BackgroundResponse(BaseModel):
    id: str
    name: str
    url: str
    thumbnail_url: str
    source: str
    position: int
    created_at: str


class RenameRequest(BaseModel):
    name: str


class GenerateRequest(BaseModel):
    prompt: str
    name: Optional[str] = None


def _to_response(bg: AvatarBackground) -> dict:
    return {
        "id": bg.id,
        "name": bg.name,
        "url": f"https://media.luminacast.com/{bg.r2_key}",
        "thumbnail_url": f"https://media.luminacast.com/{bg.thumbnail_r2_key or bg.r2_key}",
        "source": bg.source.value if hasattr(bg.source, "value") else str(bg.source),
        "position": bg.position,
        "created_at": bg.created_at.isoformat() if bg.created_at else "",
    }


async def _next_name(db: AsyncSession, avatar_id: str, prefix: str) -> str:
    count = await db.scalar(
        select(sa_func.count(AvatarBackground.id)).where(
            AvatarBackground.avatar_id == avatar_id,
            AvatarBackground.name.ilike(f"{prefix}%"),
            AvatarBackground.deleted_at.is_(None),
        )
    )
    return f"{prefix} {(count or 0) + 1}"


async def _next_position(db: AsyncSession, avatar_id: str) -> int:
    result = await db.scalar(
        select(sa_func.max(AvatarBackground.position)).where(
            AvatarBackground.avatar_id == avatar_id,
            AvatarBackground.deleted_at.is_(None),
        )
    )
    return (result or 0) + 1


async def _verify_avatar_ownership(db: AsyncSession, avatar_id: str, user: User):
    avatar = await db.scalar(
        select(Avatar).where(Avatar.id == avatar_id, Avatar.user_id == user.id)
    )
    if not avatar:
        raise HTTPException(404, "Avatar not found")
    return avatar


@router.get("/{avatar_id}/backgrounds")
async def list_backgrounds(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_avatar_ownership(db, avatar_id, user)
    rows = await db.execute(
        select(AvatarBackground).where(
            AvatarBackground.avatar_id == avatar_id,
            AvatarBackground.deleted_at.is_(None),
        ).order_by(AvatarBackground.position.asc(), AvatarBackground.created_at.asc())
    )
    backgrounds = rows.scalars().all()
    return {"backgrounds": [_to_response(bg) for bg in backgrounds]}


@router.post("/{avatar_id}/backgrounds/upload")
async def upload_background(
    avatar_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_avatar_ownership(db, avatar_id, user)

    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Only JPEG, PNG, WebP allowed")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 10MB)")

    bg_id = f"bg_{uuid.uuid4().hex[:12]}"
    r2_key = f"avatars/{avatar_id}/backgrounds/{bg_id}.jpg"

    r2 = get_r2_storage_service()
    await r2.upload_bytes(content, r2_key, content_type=file.content_type or "image/jpeg")

    bg = AvatarBackground(
        id=bg_id,
        avatar_id=avatar_id,
        user_id=user.id,
        name=await _next_name(db, avatar_id, "Upload"),
        r2_key=r2_key,
        source=BackgroundSource.UPLOAD,
        file_size_bytes=len(content),
        position=await _next_position(db, avatar_id),
    )
    db.add(bg)
    await db.commit()
    await db.refresh(bg)

    return _to_response(bg)


@router.post("/{avatar_id}/backgrounds/generate")
async def generate_background(
    avatar_id: str,
    req: GenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI-generate a 9:16 portrait background via fal.ai FLUX."""
    await _verify_avatar_ownership(db, avatar_id, user)

    if not req.prompt or len(req.prompt) < 3:
        raise HTTPException(400, "Prompt too short")

    import fal_client
    import asyncio
    os.environ["FAL_KEY"] = os.getenv("FAL_KEY", "")

    try:
        result = await asyncio.to_thread(
            fal_client.subscribe,
            "fal-ai/flux/schnell",
            arguments={
                "prompt": f"{req.prompt}, 9:16 portrait aspect ratio, no people, empty scene, photorealistic",
                "image_size": "portrait_16_9",
                "num_inference_steps": 4,
                "num_images": 1,
            },
            with_logs=False,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Background generation failed: %s", e)
        raise HTTPException(502, f"Scene generation failed: {str(e)[:200]}")

    images = result.get("images", [])
    if not images:
        raise HTTPException(502, "No image returned from Face Forge")

    image_url = images[0].get("url")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        img_content = resp.content

    bg_id = f"bg_{uuid.uuid4().hex[:12]}"
    r2_key = f"avatars/{avatar_id}/backgrounds/{bg_id}.jpg"
    r2 = get_r2_storage_service()
    await r2.upload_bytes(img_content, r2_key, content_type="image/jpeg")

    bg = AvatarBackground(
        id=bg_id,
        avatar_id=avatar_id,
        user_id=user.id,
        name=req.name or await _next_name(db, avatar_id, "Generated"),
        r2_key=r2_key,
        source=BackgroundSource.AI_GENERATED,
        generation_prompt=req.prompt,
        file_size_bytes=len(img_content),
        position=await _next_position(db, avatar_id),
    )
    db.add(bg)
    await db.commit()
    await db.refresh(bg)

    return _to_response(bg)


@router.patch("/{avatar_id}/backgrounds/{bg_id}")
async def rename_background(
    avatar_id: str,
    bg_id: str,
    req: RenameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_avatar_ownership(db, avatar_id, user)
    bg = await db.scalar(
        select(AvatarBackground).where(
            AvatarBackground.id == bg_id,
            AvatarBackground.avatar_id == avatar_id,
        )
    )
    if not bg:
        raise HTTPException(404, "Scene not found")
    if len(req.name) < 1 or len(req.name) > 80:
        raise HTTPException(400, "Name must be 1-80 chars")
    bg.name = req.name
    await db.commit()
    return _to_response(bg)


@router.delete("/{avatar_id}/backgrounds/{bg_id}")
async def delete_background(
    avatar_id: str,
    bg_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_avatar_ownership(db, avatar_id, user)
    bg = await db.scalar(
        select(AvatarBackground).where(
            AvatarBackground.id == bg_id,
            AvatarBackground.avatar_id == avatar_id,
        )
    )
    if not bg:
        raise HTTPException(404, "Scene not found")
    from datetime import datetime
    bg.deleted_at = datetime.utcnow()
    await db.commit()
    return {"deleted": True}
