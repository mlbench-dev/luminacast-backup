"""Nano Banana Pro (Google Gemini 3 Pro Image) on fal.ai.

Migration target for the FLUX Kontext image-editing calls where fidelity to a
specific reference product matters. FLUX Kontext re-imagines a referenced
object from its name (a full KOTIN PC tower came back as a hallucinated,
hand-sized graphics card); Nano Banana Pro composites the actual pixels from
one or more reference images and follows instructions literally.

Gated by ``NANO_BANANA_PRO_ENABLED`` (default OFF) so every migrated call site
keeps its exact FLUX behaviour until an operator opts in and can A/B the
output, then roll back instantly by unsetting the flag.

fal endpoints:
  * ``fal-ai/nano-banana-pro/edit`` — ``prompt`` + ``image_urls[]`` -> composed
    image. This is what the migrated sites use.
  * ``fal-ai/nano-banana-pro`` — ``prompt`` -> image (pure text-to-image; not
    wired up yet).

Both return ``{"images": [{"url": ...}], "description": ...}``.

Pricing (fal, verified 2026-09): $0.15 per image at 1K/2K, $0.30 at 4K, plus
$0.015 if web search is enabled. We request 2K (same price as 1K, more
detail) and never enable web search. FLUX Kontext Pro was $0.04, so a migrated
image is ~3.75x the provider cost — see services/cost_rates.py.
"""
from __future__ import annotations

import os

from config import settings

NANO_BANANA_EDIT_MODEL = "fal-ai/nano-banana-pro/edit"
NANO_BANANA_GENERATE_MODEL = "fal-ai/nano-banana-pro"

# String recorded on generated assets (ProductAsset.generation_model, etc.) so
# cost/analytics can tell a Nano Banana Pro image apart from a FLUX one.
NANO_BANANA_MODEL_TAG = "nano_banana_pro"

_TRUTHY = {"1", "true", "yes", "on"}


def nano_banana_pro_enabled() -> bool:
    """True when the flag-gated Nano Banana Pro image path is on (default OFF).

    Read at call time (not import) so it can be flipped without a redeploy.
    """
    raw = os.environ.get("NANO_BANANA_PRO_ENABLED")
    return bool(raw) and raw.strip().lower() in _TRUTHY


def _ensure_fal_key() -> None:
    """fal_client reads the key from the FAL_KEY env var."""
    if not os.environ.get("FAL_KEY") and getattr(settings, "FAL_API_KEY", None):
        os.environ["FAL_KEY"] = settings.FAL_API_KEY


def _first_image_url(result) -> str | None:
    if isinstance(result, dict):
        imgs = result.get("images") or []
        if imgs and isinstance(imgs[0], dict) and imgs[0].get("url"):
            return imgs[0]["url"]
        img = result.get("image")
        if isinstance(img, dict) and img.get("url"):
            return img["url"]
        if isinstance(img, str):
            return img
    return None


def _edit_args(prompt: str, image_urls, *, aspect_ratio: str, resolution: str,
               output_format: str) -> dict:
    urls = [u for u in image_urls if u]
    if not urls:
        raise ValueError("Nano Banana Pro edit needs at least one image_url")
    return {
        "prompt": prompt,
        "image_urls": urls,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "output_format": output_format,
    }


def edit_image_subscribe(
    prompt: str,
    image_urls,
    *,
    aspect_ratio: str = "auto",
    resolution: str = "2K",
    output_format: str = "jpeg",
) -> str:
    """Blocking Nano Banana Pro multi-image edit (``fal_client.subscribe``).

    ``subscribe`` blocks — call this from a worker thread
    (``asyncio.to_thread``). Returns the output image URL; raises
    ``RuntimeError`` if fal returns none.
    """
    import fal_client

    _ensure_fal_key()
    result = fal_client.subscribe(
        NANO_BANANA_EDIT_MODEL,
        arguments=_edit_args(
            prompt, image_urls, aspect_ratio=aspect_ratio,
            resolution=resolution, output_format=output_format,
        ),
    )
    url = _first_image_url(result)
    if not url:
        raise RuntimeError(f"Nano Banana Pro returned no image: {str(result)[:300]}")
    return url


async def edit_image_run_async(
    prompt: str,
    image_urls,
    *,
    aspect_ratio: str = "auto",
    resolution: str = "2K",
    output_format: str = "jpeg",
) -> str:
    """Async Nano Banana Pro multi-image edit (``fal_client.run_async``)."""
    import fal_client

    _ensure_fal_key()
    result = await fal_client.run_async(
        NANO_BANANA_EDIT_MODEL,
        arguments=_edit_args(
            prompt, image_urls, aspect_ratio=aspect_ratio,
            resolution=resolution, output_format=output_format,
        ),
    )
    url = _first_image_url(result)
    if not url:
        raise RuntimeError(f"Nano Banana Pro returned no image: {str(result)[:300]}")
    return url
