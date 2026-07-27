"""Scene-image generation for PRODUCT_DEMO motion blocks (Option A).

The product-conditioned motion bake renders a generic bottle in the
avatar's hand even with a stronger prompt, because the motion engine has
no idea what a given brand's packaging looks like (user complaint @0:24).

This service generates a still "scene image" that already contains the
ACTUAL uploaded product — using the FLUX Kontext image-to-image model with
the product reference as input — which is then handed to the motion engine
as its start frame. Because the start frame already shows the real branded
product correctly held, the resulting motion clip preserves the product
identity instead of hallucinating a stand-in.

This path is OFF by default and only runs when
``PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE=true`` so it ships safely.

Scene images are cached in R2 keyed by ``(product_id, motion_intent_hash)``
so repeated renders of the same product + motion intent never re-pay for
generation.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import fal_client
import sentry_sdk

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Same FLUX Kontext model the avatar-frame editor uses — it is the I2I model
# already wired into fal-client in this codebase.
FAL_KONTEXT_MODEL = "fal-ai/flux-pro/kontext"

_TRUTHY = {"1", "true", "yes", "on"}


def flux_kontext_scene_enabled() -> bool:
    """True when the flag-gated FLUX Kontext scene path is enabled.

    Reads ``PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE`` (default false). Ships
    safely off — an operator opts in by setting it truthy.
    """
    raw = os.environ.get("PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def _ensure_fal_key() -> None:
    """Ensure FAL_KEY env var is set from app config (fal_client reads it)."""
    if not os.environ.get("FAL_KEY") and settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = settings.FAL_API_KEY


def motion_intent_hash(motion_prompt: str | None) -> str:
    """Stable short hash of the motion intent, used in the cache key.

    Normalised (stripped + lowercased) so trivially different whitespace or
    casing maps to the same cached scene image.
    """
    norm = (motion_prompt or "").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def scene_image_cache_key(product_id: str, motion_prompt: str | None) -> str:
    """R2 key for the cached scene image of (product_id, motion_intent)."""
    return (
        f"scene_images/{product_id}/{motion_intent_hash(motion_prompt)}.jpg"
    )


def _build_scene_prompt(motion_prompt: str | None) -> str:
    """Compose the FLUX Kontext I2I prompt.

    Leads with an instruction to preserve the input product exactly, then
    places it into a held, in-scene composition derived from the motion
    intent. The product image is the I2I input, so "keep the product
    exactly as shown" is what stops FLUX from re-styling the packaging.
    """
    motion = (motion_prompt or "").strip()
    scene = motion or "a person holding the product in a bright lifestyle setting"
    return (
        "A person holding this exact product in their hand, product centered "
        "and clearly visible with its label readable. "
        f"Scene: {scene}. "
        "Keep the product's shape, color, and label exactly as shown in the "
        "input image — do not redesign or restyle the packaging. "
        "Photorealistic, natural lighting, high quality."
    )


async def generate_scene_image(
    product_asset_url: str,
    motion_prompt: str,
    *,
    product_id: str | None = None,
) -> bytes:
    """Generate a scene still containing the real product, return its bytes.

    Args:
        product_asset_url: Public URL of the product reference image (I2I input).
        motion_prompt: The block's motion intent — shapes the composition.
        product_id: When provided, the result is cached in R2 keyed by
            (product_id, motion_intent_hash) and reused on subsequent calls.

    Returns:
        Raw JPEG bytes of the generated scene image.

    Raises:
        ValueError: If no usable product image URL is provided or FLUX returns
            no image.
    """
    if not product_asset_url:
        raise ValueError("generate_scene_image requires a product_asset_url")

    from services.r2_storage import get_r2_storage_service

    r2 = get_r2_storage_service()
    cache_key = (
        scene_image_cache_key(product_id, motion_prompt) if product_id else None
    )

    # Cache hit — return the previously generated scene image.
    if cache_key:
        try:
            if await r2.key_exists(cache_key):
                local_url = r2.get_public_url(cache_key)
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(local_url)
                    resp.raise_for_status()
                    logger.info(
                        "scene_image cache hit: product=%s key=%s",
                        product_id, cache_key,
                    )
                    return resp.content
        except Exception as e:
            # A cache read failure must never block generation — fall through.
            sentry_sdk.capture_exception(e)

    _ensure_fal_key()
    prompt = _build_scene_prompt(motion_prompt)

    with sentry_sdk.start_span(op="fal_ai", description="scene image generate") as span:
        span.set_data("product_id", product_id or "")
        _fal_start = time.monotonic()
        result = await fal_client.run_async(
            FAL_KONTEXT_MODEL,
            arguments={
                "prompt": prompt,
                "image_url": product_asset_url,
                "guidance_scale": 3.5,
                "num_inference_steps": 28,
                "output_format": "jpeg",
            },
        )

        images = (result or {}).get("images", [])
        if not images:
            raise ValueError("scene image generation returned no images")
        scene_url = images[0].get("url", "")
        if not scene_url:
            raise ValueError("scene image generation returned an entry with no URL")

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(scene_url)
            resp.raise_for_status()
            scene_bytes = resp.content

        span.set_data("status", "success")

    # Best-effort cache write — generation already succeeded, so a write
    # failure should not fail the render.
    if cache_key:
        try:
            await r2.upload_bytes(scene_bytes, cache_key, content_type="image/jpeg")
            logger.info(
                "scene_image cached: product=%s key=%s", product_id, cache_key
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)

    # TODO: user_id isn't threaded through this call chain yet (it originates
    # deep in cast_render._render_async, keyed by render_id, not user_id) —
    # passing None here is deliberate (nullable column, safe insert) rather
    # than the previous "" (violated the FK on every call). Follow-up:
    # resolve user_id from the render's cast/avatar for real attribution.
    from services.usage_logger import log_api_usage
    await log_api_usage(
        user_id=None, service="scene_image", operation="product_scene",
        success=True, duration_seconds=round(time.monotonic() - _fal_start, 1),
        cost_cents=4,
    )

    return scene_bytes
