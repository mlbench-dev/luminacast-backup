"""Product ai_media endpoints — split from the former routers/products.py."""

import logging
import os
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

async def _resolve_product_source_image(product: Product, db: AsyncSession, r2) -> str:
    """Return a public R2 URL for the product's real reference photo.

    Prefers the stored cover; for TikTok-sourced products with no cover yet,
    materializes the TikTok CDN cover to R2 first. Returns "" when no
    reference photo exists — callers that need one (image-to-image /
    image-to-video generation) should treat that as a 400.
    """
    if product.cover_image_key:
        return r2.get_public_url(product.cover_image_key)

    if product.tiktok_product_id:
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
                        return r2.get_public_url(r2_key)
            except Exception:
                pass

    return ""

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

    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise HTTPException(503, "AI generation unavailable: FAL_API_KEY not configured")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    source_image_url = await _resolve_product_source_image(product, db, r2)
    if not source_image_url:
        raise HTTPException(400, "Product needs a cover image. Upload one or pick a different product.")

    scene_prompts = {
        "product_shot": "Professional studio product photography on a clean white background with soft studio lighting, 4K quality.",
        "lifestyle": f"{product.name} shown in a stylish lifestyle setting with natural lighting, editorial photography style.",
        "swatch": f"Extreme close-up macro photography of {product.name}, showing fine texture and material detail.",
    }
    # Scene/style instruction is stated first (FLUX Kontext weighs earlier
    # instructions more heavily), then the identity-preservation clause —
    # same ordering as services/flux_kontext.py's avatar-frame edits.
    scene_instruction = (
        custom_prompt.strip() if custom_prompt and custom_prompt.strip()
        else scene_prompts.get(style, scene_prompts["product_shot"])
    )
    preserve_clause = (
        "Keep the exact same product from the reference photo completely unchanged — "
        "identical shape, color, materials, design, and any logos or text. "
        "Do not restyle, redesign, or recolor the product."
    )
    prompt = f"{scene_instruction} {preserve_clause}"

    try:
        import asyncio
        import fal_client

        def _run_flux_kontext():
            os.environ["FAL_KEY"] = app_settings.FAL_API_KEY
            return fal_client.subscribe(
                "fal-ai/flux-pro/kontext",
                arguments={
                    "prompt": prompt,
                    "image_url": source_image_url,
                    "guidance_scale": 3.5,
                    "num_inference_steps": 28,
                    "output_format": "jpeg",
                },
            )

        result = await asyncio.to_thread(_run_flux_kontext)
        image_url = result["images"][0]["url"]

        # Download and upload to R2
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            asset_id = f"pa_{uuid.uuid4().hex[:12]}"
            r2_key = f"products/{product_id}/assets/{asset_id}.jpg"
            await r2.upload_bytes(resp.content, r2_key, "image/jpeg")

            asset = ProductAsset(
                id=asset_id, product_id=product_id, user_id=ctx.workspace_owner_id,
                asset_type="ai_generated_image", media_type="image",
                r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
                file_size_bytes=len(resp.content),
                generation_prompt=prompt, generation_model="flux_pro_kontext",
            )
            db.add(asset)
            await db.commit()

        return {"id": asset.id, "r2_url": asset.r2_url, "prompt": prompt}

    except Exception as e:
        raise HTTPException(500, f"AI image generation failed: {str(e)[:200]}")

_KLING_MODELS = {
    "pro": "fal-ai/kling-video/v1.5/pro/image-to-video",
    "fast": "fal-ai/kling-video/v1.6/standard/image-to-video",
}

_KLING_SUPPORTS_ASPECT_RATIO = {"pro"}

_KLING_CFG_SCALE = {"fast": 0.8}

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

    prompts = {
        "product_showcase": f"Slow cinematic rotation of {product.name}, studio lighting, white background, smooth camera movement, product photography style, 4K quality",
        "lifestyle": f"{product.name} being used naturally, soft natural lighting, lifestyle photography, warm tones",
        # Kling's image-to-video locks frame 1 to the product's actual cover
        # photo — there is no box in that photo. Any prompt asking the model
        # to materialize a box, cover the product, then remove it forces it
        # to hallucinate an object that was never in frame, which it does
        # badly (a half-formed cover-and-remove, or a backwards
        # shoe-then-box-then-no-box sequence). Framing the "reveal" as
        # camera/lighting motion instead of object insertion plays to what
        # these models can actually do reliably.
        "unboxing": f"{product.name} sits on display exactly as shown. Camera starts close and slightly above, then pulls back and rises in a smooth motion as studio lighting brightens, revealing the full product in a satisfying unveiling shot, clean background",
        "comparison": f"Before and after using {product.name}, split screen effect, dramatic transformation",
    }
    prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else prompts.get(style, prompts["product_showcase"])

    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise HTTPException(503, "AI generation unavailable: FAL_API_KEY not configured")

    # Need a source image — try R2 cover, fall back to TikTok CDN
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    source_image_url = await _resolve_product_source_image(product, db, r2)
    if not source_image_url:
        raise HTTPException(400, "Product needs a cover image. Upload one or pick a different product.")

    kling_tier = quality if quality in _KLING_MODELS else "pro"
    kling_model = _KLING_MODELS[kling_tier]
    # Derived from the model id itself (not hand-typed) so the stored label
    # can't drift out of sync with _KLING_MODELS on a future version bump.
    _model_parts = kling_model.split("/")
    kling_model_label = f"kling_{_model_parts[2]}_{_model_parts[3]}"

    try:
        import asyncio
        import fal_client

        def _run_kling():
            os.environ["FAL_KEY"] = app_settings.FAL_API_KEY
            kling_args = {
                "prompt": prompt,
                "image_url": source_image_url,
                "duration": str(duration_seconds),
            }
            if kling_tier in _KLING_SUPPORTS_ASPECT_RATIO:
                kling_args["aspect_ratio"] = "9:16"
            if kling_tier in _KLING_CFG_SCALE:
                kling_args["cfg_scale"] = _KLING_CFG_SCALE[kling_tier]
            return fal_client.subscribe(kling_model, arguments=kling_args)

        result = await asyncio.to_thread(_run_kling)
        video_url = result["video"]["url"]

        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(video_url)
            asset_id = f"pa_{uuid.uuid4().hex[:12]}"
            r2_key = f"products/{product_id}/assets/{asset_id}.mp4"
            await r2.upload_bytes(resp.content, r2_key, "video/mp4")

            asset = ProductAsset(
                id=asset_id, product_id=product_id, user_id=ctx.workspace_owner_id,
                asset_type="ai_generated_video", media_type="video",
                r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
                duration_seconds=duration_seconds,
                file_size_bytes=len(resp.content),
                generation_prompt=prompt, generation_model=kling_model_label,
            )
            db.add(asset)
            await db.commit()

        return {"id": asset.id, "r2_url": asset.r2_url, "prompt": prompt, "duration": duration_seconds}

    except Exception as e:
        raise HTTPException(500, f"AI video generation failed: {str(e)[:200]}")

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
