"""Generated videos endpoints for the My Videos | Photos Generated sub-folder."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from routers.auth import get_current_user
from database import get_db
from models import User, AIGeneratedVideo
from services.generated_videos_service import GeneratedVideosService
from services.inspire_me_service import InspireMeService
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["generated_videos"])


class GenerateVideoRequest(BaseModel):
    engine: Literal["kling", "wan"]
    mode: Literal["text_to_video", "image_to_video"]
    prompt: str = Field(..., min_length=1, max_length=2000)
    reference_image_url: str | None = None
    reference_image_r2_key: str | None = None
    duration: int = Field(5, ge=5, le=10)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    negative_prompt: str | None = None
    seed: int | None = None
    camera_preset: str | None = None
    num_videos: int = Field(1, ge=1, le=4)


class InspireMeRequest(BaseModel):
    user_idea: str = Field("", max_length=2000)
    engine: Literal["kling", "wan"]
    mode: Literal["text_to_video", "image_to_video"]
    duration: int = 5
    aspect_ratio: str = "16:9"
    camera_preset: str | None = None
    reference_image_url: str | None = None


@router.post("/generate")
async def generate_video(
    req: GenerateVideoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.mode == "image_to_video" and not req.reference_image_url:
        raise HTTPException(400, "image_to_video requires reference_image_url")
    if req.engine == "wan" and req.camera_preset and req.camera_preset != "none":
        req.camera_preset = None

    service = GeneratedVideosService()
    try:
        result = await service.generate_and_store(
            user_id=str(user.id),
            engine=req.engine,
            mode=req.mode,
            prompt=req.prompt,
            reference_image_url=req.reference_image_url,
            duration=req.duration,
            aspect_ratio=req.aspect_ratio,
            negative_prompt=req.negative_prompt,
            seed=req.seed,
            camera_preset=req.camera_preset,
            num_videos=req.num_videos,
        )
    except Exception as e:
        logger.exception("Video generation failed for user %s", user.id)
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Generation failed: {str(e)[:200]}")

    r2 = get_r2_storage_service()
    batch_id = f"vbatch_{uuid.uuid4().hex[:12]}"
    per_video_cost = result["cost_usd"] / max(len(result["r2_keys"]), 1)

    created = []
    for r2_key in result["r2_keys"]:
        video = AIGeneratedVideo(
            user_id=str(user.id),
            r2_key=r2_key,
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            engine_used=result["engine_used"],
            mode=req.mode,
            duration_seconds=req.duration,
            aspect_ratio=req.aspect_ratio,
            camera_preset=req.camera_preset,
            reference_image_r2_key=req.reference_image_r2_key,
            seed=req.seed,
            cost_usd=per_video_cost,
            batch_id=batch_id,
        )
        db.add(video)
        await db.flush()
        created.append({
            "id": str(video.id),
            "url": r2.get_public_url(r2_key),
            "prompt": req.prompt,
            "engine_used": result["engine_used"],
            "mode": req.mode,
            "duration": req.duration,
            "aspect_ratio": req.aspect_ratio,
            "batch_id": batch_id,
            "created_at": video.created_at.isoformat() if video.created_at else datetime.now(timezone.utc).isoformat(),
        })
    await db.commit()
    return {
        "videos": created,
        "engine_used": result["engine_used"],
        "batch_id": batch_id,
        "total_cost_usd": result["cost_usd"],
    }


@router.get("/generated")
async def list_generated_videos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = (
        select(AIGeneratedVideo)
        .where(
            AIGeneratedVideo.user_id == str(user.id),
            AIGeneratedVideo.deleted_at.is_(None),
        )
        .order_by(AIGeneratedVideo.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    r2 = get_r2_storage_service()
    return {
        "videos": [
            {
                "id": str(row.id),
                "url": r2.get_public_url(row.r2_key),
                "prompt": row.prompt,
                "negative_prompt": row.negative_prompt,
                "engine_used": row.engine_used,
                "mode": row.mode,
                "duration": row.duration_seconds,
                "aspect_ratio": row.aspect_ratio,
                "camera_preset": row.camera_preset,
                "seed": row.seed,
                "batch_id": row.batch_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.delete("/generated/{video_id}")
async def delete_generated_video(
    video_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    video = await db.get(AIGeneratedVideo, video_id)
    if not video or str(video.user_id) != str(user.id) or video.deleted_at is not None:
        raise HTTPException(404, "Video not found")
    video.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"deleted": True}


@router.post("/inspire-me")
async def inspire_me(
    req: InspireMeRequest,
    user: User = Depends(get_current_user),
):
    """Engine-aware prompt enhancement."""
    service = InspireMeService()

    reference_description = None
    if req.mode == "image_to_video" and req.reference_image_url:
        try:
            reference_description = await service.describe_reference_image(
                req.reference_image_url
            )
        except Exception as e:
            logger.warning("Reference image description failed: %s", e)
            sentry_sdk.capture_exception(e)

    try:
        enhanced = await service.enhance(
            user_idea=req.user_idea,
            engine=req.engine,
            mode=req.mode,
            duration=req.duration,
            aspect_ratio=req.aspect_ratio,
            camera_preset=req.camera_preset,
            reference_image_description=reference_description,
        )
        return {"enhanced_prompt": enhanced}
    except Exception as e:
        logger.exception("Inspire Me failed for user %s", user.id)
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Inspire Me failed: {str(e)[:200]}")


@router.post("/upload-reference")
async def upload_reference_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload a reference image for image-to-video generation. Returns the R2 URL."""
    import uuid as _uuid
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files are accepted")
    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 20MB")
    r2 = get_r2_storage_service()
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    r2_key = f"users/{user.id}/generated_videos/references/{_uuid.uuid4().hex}.{ext}"
    await r2.upload_bytes(file_bytes, r2_key, file.content_type or "image/jpeg")
    return {"url": r2.get_public_url(r2_key), "r2_key": r2_key}
