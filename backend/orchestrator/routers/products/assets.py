"""Product assets endpoints — split from the former routers/products.py."""

import logging
import os
import uuid
import hashlib
from typing import Optional, List
from urllib.parse import quote
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, update as sa_update
from pydantic import BaseModel
import sentry_sdk
from database import get_db
from models.user import User, TeamRole
from models.product import Product
from models.product_asset import ProductAsset
from models.cast import Cast, CastStatus, CastProduct
from models.block import Block
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

class AssetResponse(BaseModel):
    id: str
    asset_type: str
    media_type: str
    r2_key: str
    r2_url: str
    duration_seconds: float = 0
    width: int = 0
    height: int = 0
    file_size_bytes: int = 0
    generation_prompt: str = ""
    generation_model: str = ""
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}

@router.post("/{product_id}/assets/upload")
async def upload_asset(
    product_id: str,
    file: UploadFile = File(...),
    asset_type: str = Query("product_shot"),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    content = await file.read()
    asset_id = f"pa_{uuid.uuid4().hex[:12]}"

    # Determine media type from content type
    ct = file.content_type or ""
    if "video" in ct:
        media_type = "video"
        ext = "mp4"
    elif "image" in ct:
        media_type = "image"
        ext = "jpg" if "jpeg" in ct else "png"
    else:
        media_type = "image"
        ext = "bin"

    r2_key = f"products/{product_id}/assets/{asset_id}.{ext}"
    await r2.upload_bytes(content, r2_key, ct or f"{media_type}/{ext}")

    asset = ProductAsset(
        id=asset_id,
        product_id=product_id,
        user_id=ctx.workspace_owner_id,        asset_type=asset_type,
        media_type=media_type,
        r2_key=r2_key,
        r2_url=r2.get_public_url(r2_key),
        file_size_bytes=len(content),
    )
    db.add(asset)

    # asset_type="cover" is the frontend's signal that this upload IS the
    # product's cover, not just another gallery shot — every cover_image_url
    # in every response is derived solely from product.cover_image_key, so
    # without this the upload succeeded but the cover never visibly changed.
    if asset_type == "cover" and media_type == "image":
        product.cover_image_key = r2_key

    await db.commit()
    await db.refresh(asset)

    try:
        await audit_log.record(
            db, user_id=user.id, action="product.asset_upload", entity_type="product",
            entity_id=product_id, after={"asset_id": asset.id, "asset_type": asset.asset_type, "media_type": asset.media_type},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "media_type": asset.media_type,
        "r2_key": asset.r2_key,
        "r2_url": asset.r2_url,
        "file_size_bytes": asset.file_size_bytes,
    }

@router.post("/{product_id}/assets/record")
async def record_asset(
    product_id: str,
    file: UploadFile = File(...),
    asset_type: str = Query("demo"),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a recorded video/audio blob as an asset."""
    return await upload_asset(product_id, file, asset_type, user, db)

@router.get("/{product_id}/assets")
async def list_product_assets(
    product_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return all assets for a product, ordered for gallery / carousel display.

    Ordering: position ascending (cover at 0), then created_at ascending so
    legacy rows without explicit position appear in import order.
    """
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    result = await db.execute(
        select(ProductAsset)
        .where(ProductAsset.product_id == product_id)
        .order_by(ProductAsset.position.asc(), ProductAsset.created_at.asc())
    )
    assets = result.scalars().all()
    return [
        {
            "id": a.id,
            "asset_type": a.asset_type,
            "media_type": a.media_type,
            "r2_key": a.r2_key,
            "r2_url": a.r2_url or "",
            "position": a.position or 0,
            "duration_seconds": a.duration_seconds or 0,
            "width": a.width or 0,
            "height": a.height or 0,
            "file_size_bytes": a.file_size_bytes or 0,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in assets
    ]

@router.delete("/{product_id}/assets/{asset_id}", status_code=204)
async def delete_asset(
    product_id: str,
    asset_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(ProductAsset, asset_id)
    if not asset or asset.product_id != product_id:
        raise HTTPException(404, "Asset not found")
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")
    try:
        await audit_log.record(
            db, user_id=user.id, action="product.asset_delete", entity_type="product",
            entity_id=product_id, before={"asset_id": asset_id},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)

    # Detach any cast blocks that reference this asset (AI b-roll sets
    # Block.video_asset_id / image_asset_id to a ProductAsset id). These are
    # plain FKs with no ON DELETE, so deleting a referenced asset 500s with an
    # IntegrityError — which the product page surfaced as "the X does nothing".
    # Nulling the refs makes those blocks fall back to their stock clip.
    for _col in ("video_asset_id", "image_asset_id"):
        await db.execute(
            sa_update(Block).where(getattr(Block, _col) == asset_id).values({_col: None})
        )

    await db.delete(asset)
    await db.commit()
