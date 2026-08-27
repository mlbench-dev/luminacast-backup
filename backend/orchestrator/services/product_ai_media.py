"""Shared AI product-media generation, extracted from routers/products/ai_media.py
so both the Product page's own "AI Generate" buttons and the Cast Builder
(Setup-tab default / Script-tab per-block "Generate" button) call the exact
same FLUX Kontext / Kling pipeline instead of two divergent copies.
"""

import os
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.product import Product
from models.product_asset import ProductAsset


async def resolve_product_source_image(product: Product, db: AsyncSession, r2) -> str:
    """Return a public R2 URL for the product's real reference photo.

    Prefers the stored cover; for TikTok-sourced products with no cover yet,
    materializes the TikTok CDN cover to R2 first. Returns "" when no
    reference photo exists — callers that need one (image-to-image /
    image-to-video generation) should treat that as a usage error.
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


_IMAGE_SCENE_PROMPTS = {
    "product_shot": "Professional studio product photography on a clean white background with soft studio lighting, 4K quality.",
    "lifestyle": "{name} shown in a stylish lifestyle setting with natural lighting, editorial photography style.",
    "swatch": "Extreme close-up macro photography of {name}, showing fine texture and material detail.",
}


class ProductAiMediaError(Exception):
    """Raised when generation can't proceed (missing key, no cover image) or
    the fal.ai call itself fails. Callers decide how to surface/fall back."""


async def generate_ai_image_asset(
    product: Product,
    db: AsyncSession,
    owner_id: str,
    style: str = "product_shot",
    custom_prompt: str = "",
) -> ProductAsset:
    """Generate a styled product photo via FLUX Kontext (image-to-image) and
    persist it as a new ``ProductAsset`` row tagged ``ai_generated_image``.

    Kontext edits the product's *real* cover photo instead of hallucinating
    a fresh product from text alone. Raises ``ProductAiMediaError`` on any
    failure (no FAL key, no reference photo, generation error) — callers
    decide whether that's a 400/503/500 (product-page button) or a silent
    fall-back to existing stock media (cast-builder auto-generation).
    """
    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise ProductAiMediaError("AI generation unavailable: FAL_API_KEY not configured")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    source_image_url = await resolve_product_source_image(product, db, r2)
    if not source_image_url:
        raise ProductAiMediaError("Product needs a cover image. Upload one or pick a different product.")

    scene_instruction = (
        custom_prompt.strip() if custom_prompt and custom_prompt.strip()
        else _IMAGE_SCENE_PROMPTS.get(style, _IMAGE_SCENE_PROMPTS["product_shot"]).format(name=product.name)
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

        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            asset_id = f"pa_{uuid.uuid4().hex[:12]}"
            r2_key = f"products/{product.id}/assets/{asset_id}.jpg"
            await r2.upload_bytes(resp.content, r2_key, "image/jpeg")

            asset = ProductAsset(
                id=asset_id, product_id=product.id, user_id=owner_id,
                asset_type="ai_generated_image", media_type="image",
                r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
                file_size_bytes=len(resp.content),
                generation_prompt=prompt, generation_model="flux_pro_kontext",
            )
            db.add(asset)
            await db.commit()
            return asset
    except ProductAiMediaError:
        raise
    except Exception as e:
        raise ProductAiMediaError(f"AI image generation failed: {str(e)[:200]}") from e


_VIDEO_PROMPTS = {
    "product_showcase": "Slow cinematic rotation of {name}, studio lighting, white background, smooth camera movement, product photography style, 4K quality",
    "lifestyle": "{name} being used naturally, soft natural lighting, lifestyle photography, warm tones",
    "unboxing": (
        "{name} sits on display exactly as shown. Camera starts close and slightly above, "
        "then pulls back and rises in a smooth motion as studio lighting brightens, revealing "
        "the full product in a satisfying unveiling shot, clean background"
    ),
    "comparison": "Before and after using {name}, split screen effect, dramatic transformation",
}

_KLING_MODELS = {
    "pro": "fal-ai/kling-video/v1.5/pro/image-to-video",
    "fast": "fal-ai/kling-video/v1.6/standard/image-to-video",
}
_KLING_SUPPORTS_ASPECT_RATIO = {"pro"}
_KLING_CFG_SCALE = {"fast": 0.8}


async def generate_ai_video_asset(
    product: Product,
    db: AsyncSession,
    owner_id: str,
    style: str = "product_showcase",
    duration_seconds: int = 5,
    custom_prompt: str = "",
    quality: str = "pro",
) -> ProductAsset:
    """Generate a short product-only video via fal.ai Kling image-to-video
    and persist it as a new ``ProductAsset`` row tagged ``ai_generated_video``.

    Raises ``ProductAiMediaError`` on any failure (no FAL key, no reference
    photo, generation error).
    """
    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise ProductAiMediaError("AI generation unavailable: FAL_API_KEY not configured")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    source_image_url = await resolve_product_source_image(product, db, r2)
    if not source_image_url:
        raise ProductAiMediaError("Product needs a cover image. Upload one or pick a different product.")

    prompt = (
        custom_prompt.strip() if custom_prompt and custom_prompt.strip()
        else _VIDEO_PROMPTS.get(style, _VIDEO_PROMPTS["product_showcase"]).format(name=product.name)
    )

    kling_tier = quality if quality in _KLING_MODELS else "pro"
    kling_model = _KLING_MODELS[kling_tier]
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
            r2_key = f"products/{product.id}/assets/{asset_id}.mp4"
            await r2.upload_bytes(resp.content, r2_key, "video/mp4")

            asset = ProductAsset(
                id=asset_id, product_id=product.id, user_id=owner_id,
                asset_type="ai_generated_video", media_type="video",
                r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
                duration_seconds=duration_seconds,
                file_size_bytes=len(resp.content),
                generation_prompt=prompt, generation_model=kling_model_label,
            )
            db.add(asset)
            await db.commit()
            return asset
    except ProductAiMediaError:
        raise
    except Exception as e:
        raise ProductAiMediaError(f"AI video generation failed: {str(e)[:200]}") from e
