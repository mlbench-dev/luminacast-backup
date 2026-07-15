"""Generated photos endpoints — scoped to the My Videos | Photos feature."""
import logging
from datetime import datetime, timezone

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from routers.auth import get_current_user
from database import get_db
from models import User, AIGeneratedPhoto
from services.generated_photos_service import GeneratedPhotosService
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/photos", tags=["generated_photos"])


class GeneratePhotoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative_prompt: str | None = None
    width: int = Field(1024, ge=512, le=2048)
    height: int = Field(1024, ge=512, le=2048)
    num_images: int = Field(1, ge=1, le=4)
    seed: int | None = None


@router.post("/generate")
async def generate_photo(
    req: GeneratePhotoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.width % 64 != 0 or req.height % 64 != 0:
        raise HTTPException(400, "width and height must be multiples of 64")

    service = GeneratedPhotosService()
    try:
        result = await service.generate_and_store(
            user_id=str(user.id),
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            width=req.width,
            height=req.height,
            num_images=req.num_images,
            seed=req.seed,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception(f"Photo generation failed for user {user.id}")
        raise HTTPException(500, "Generation failed across all tiers")

    r2 = get_r2_storage_service()
    per_image_cost = result["cost_usd"] / max(len(result["r2_keys"]), 1)

    created = []
    for r2_key in result["r2_keys"]:
        photo = AIGeneratedPhoto(
            user_id=user.id,
            r2_key=r2_key,
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            width=req.width,
            height=req.height,
            seed=req.seed,
            engine_used=result["engine_used"],
            tier=result["tier"],
            model_variant=result["model_variant"],
            cost_usd=per_image_cost,
        )
        db.add(photo)
        await db.flush()
        created.append(
            {
                "id": str(photo.id),
                "url": r2.get_public_url(r2_key),
                "prompt": req.prompt,
                "width": req.width,
                "height": req.height,
                "engine_used": result["engine_used"],
                "created_at": photo.created_at.isoformat() if photo.created_at else datetime.now(timezone.utc).isoformat(),
            }
        )
    await db.commit()
    return {
        "photos": created,
        "engine_used": result["engine_used"],
        "tier": result["tier"],
    }


@router.get("/generated")
async def list_generated_photos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = (
        select(AIGeneratedPhoto)
        .where(
            AIGeneratedPhoto.user_id == user.id,
            AIGeneratedPhoto.deleted_at.is_(None),
        )
        .order_by(AIGeneratedPhoto.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    r2 = get_r2_storage_service()
    return {
        "photos": [
            {
                "id": str(row.id),
                "url": r2.get_public_url(row.r2_key),
                "prompt": row.prompt,
                "negative_prompt": row.negative_prompt,
                "width": row.width,
                "height": row.height,
                "seed": row.seed,
                "engine_used": row.engine_used,
                "tier": row.tier,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.delete("/generated/{photo_id}")
async def delete_generated_photo(
    photo_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    photo = await db.get(AIGeneratedPhoto, photo_id)
    if not photo or photo.user_id != user.id or photo.deleted_at is not None:
        raise HTTPException(404, "Photo not found")
    photo.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"deleted": True}
