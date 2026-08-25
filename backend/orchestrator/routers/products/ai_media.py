"""Product ai_media endpoints — split from the former routers/products.py."""

import logging
import uuid
import hashlib
from typing import Optional, List
from urllib.parse import quote
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
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

@router.post("/{product_id}/generate-overlay")
async def generate_overlay(
    product_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate price/discount overlay image."""
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    # Generate simple price tag overlay as SVG → PNG via placeholder
    price_text = f"${product.price:.2f}"
    discount_text = f"{int(product.discount_percent)}% OFF" if product.discount_percent else ""

    asset_id = f"pa_{uuid.uuid4().hex[:12]}"
    overlay_config = {
        "type": "price_tag",
        "price": price_text,
        "discount": discount_text,
        "product_name": product.name,
        "style": "modern",
    }

    # For now create a config-only overlay (frontend renders it)
    r2_key = f"products/{product_id}/overlays/{asset_id}.json"
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    import json
    await r2.upload_bytes(
        json.dumps(overlay_config).encode(),
        r2_key,
        "application/json",
    )

    asset = ProductAsset(
        id=asset_id,
        product_id=product_id,
        user_id=ctx.workspace_owner_id,        asset_type="price_overlay",
        media_type="overlay",
        r2_key=r2_key,
        r2_url=r2.get_public_url(r2_key),
        overlay_config=overlay_config,
    )
    db.add(asset)
    await db.commit()

    return {
        "id": asset.id,
        "overlay_config": overlay_config,
        "r2_url": asset.r2_url,
    }

@router.post("/{product_id}/generate-ai-images")
async def generate_ai_images(
    product_id: str,
    style: str = Body("product_shot"),
    custom_prompt: str = Body(""),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate styled product photos via FLUX Kontext (image-to-image).

    Kontext edits the product's *real* cover photo instead of hallucinating
    a fresh product from text alone — FLUX schnell (pure text-to-image) was
    generating a different-looking product (wrong design/color) every time
    because it had no reference to what the product actually looks like.
    """
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    from services.product_ai_media import generate_ai_image_asset, ProductAiMediaError

    try:
        asset = await generate_ai_image_asset(
            product, db, ctx.workspace_owner_id, style=style, custom_prompt=custom_prompt,
        )
    except ProductAiMediaError as e:
        msg = str(e)
        status = 503 if "FAL_API_KEY" in msg else 400 if "cover image" in msg else 500
        raise HTTPException(status, msg)

    return {"id": asset.id, "r2_url": asset.r2_url, "prompt": asset.generation_prompt}

@router.post("/{product_id}/generate-ai-video")
async def generate_ai_video(
    product_id: str,
    style: str = Body("product_showcase"),
    duration_seconds: int = Body(5),
    custom_prompt: str = Body(""),
    quality: str = Body("pro"),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a short product video using fal.ai image-to-video."""
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    from services.product_ai_media import generate_ai_video_asset, ProductAiMediaError

    try:
        asset = await generate_ai_video_asset(
            product, db, ctx.workspace_owner_id, style=style,
            duration_seconds=duration_seconds, custom_prompt=custom_prompt, quality=quality,
        )
    except ProductAiMediaError as e:
        msg = str(e)
        status = 503 if "FAL_API_KEY" in msg else 400 if "cover image" in msg else 500
        raise HTTPException(status, msg)

    return {"id": asset.id, "r2_url": asset.r2_url, "prompt": asset.generation_prompt, "duration": duration_seconds}

@router.post("/{product_id}/remove-background")
async def remove_product_background(
    product_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Remove background from product cover image using rembg.
    Creates a new asset with transparent background (PNG).
    """
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    # Materialize TikTok cover to R2 if needed
    if not product.cover_image_key and product.tiktok_product_id:
        from models.trending_product import TrendingProduct
        tp = await db.scalar(
            select(TrendingProduct).where(
                TrendingProduct.tiktok_product_id == product.tiktok_product_id,
                TrendingProduct.cover_image_url != "",
            )
        )
        if tp and tp.cover_image_url:
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(tp.cover_image_url, headers={"Referer": ""})
                    if resp.status_code == 200 and len(resp.content) > 500:
                        r2_key = f"products/covers/{product.tiktok_product_id}.jpg"
                        await r2.upload_bytes(resp.content, r2_key, "image/jpeg")
                        product.cover_image_key = r2_key
                        await db.commit()
            except Exception:
                pass

    if not product.cover_image_key:
        raise HTTPException(400, "Product has no cover image")

    # Download cover image
    import httpx
    cover_url = r2.get_public_url(product.cover_image_key)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(cover_url)
        if resp.status_code != 200:
            raise HTTPException(500, "Failed to download cover image")
        image_bytes = resp.content

    # Remove background (CPU-intensive, offload to thread to avoid blocking event loop)
    try:
        from rembg import remove as rembg_remove
        import asyncio
        result_bytes = await asyncio.to_thread(rembg_remove, image_bytes)
    except ImportError:
        raise HTTPException(500, "rembg not installed on server")
    except Exception as e:
        raise HTTPException(500, f"Background removal failed: {str(e)[:200]}")

    # Upload as PNG (with transparency)
    asset_id = f"pa_{uuid.uuid4().hex[:12]}"
    r2_key = f"products/{product_id}/assets/{asset_id}_nobg.png"
    await r2.upload_bytes(result_bytes, r2_key, "image/png")

    asset = ProductAsset(
        id=asset_id, product_id=product_id, user_id=ctx.workspace_owner_id,
        asset_type="background_removed", media_type="image",
        r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
        file_size_bytes=len(result_bytes),
    )
    db.add(asset)
    await db.commit()

    return {"id": asset.id, "r2_url": asset.r2_url, "type": "background_removed"}
