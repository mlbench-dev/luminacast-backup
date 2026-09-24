"""AI-generated *scene* b-roll — a text-to-video clip from a beat's own
stock-media query, for when Pexels keyword search only returns loosely-related
footage.

This is the product-less sibling of ``services.product_ai_media`` /
``tasks.product_broll_tasks``: those turn the cast's PRODUCT photo into a
product shot (Kling image-to-video). When a b-roll beat has no product to
anchor on, this generates the described shot directly (Kling text-to-video) at
the layout's aspect ratio, so the clip actually matches the script instead of
whatever the keyword search surfaced.

Flag-gated: inert unless ``AI_BROLL_ENABLED`` is truthy (rollout pattern
mirrors ``NANO_BANANA_PRO_ENABLED``). The caller keeps the Pexels clip as the
fallback on any failure.
"""
from __future__ import annotations

import os
import uuid

import httpx

_TRUTHY = {"1", "true", "yes", "on"}

# Kling text-to-video on fal. v2.5-turbo/pro = smoother, less-warpy motion
# than 2.1/1.6. Overridable (AI_BROLL_MODEL) so it can be swapped for a
# cheaper tier — fal-ai/kling-video/v1.6/standard/text-to-video — or rolled
# back without a deploy.
_DEFAULT_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"

_VALID_ASPECT = {"9:16", "16:9", "1:1"}


class AiBrollError(RuntimeError):
    """Any failure generating a scene b-roll clip. Caller falls back to stock."""


def ai_broll_enabled() -> bool:
    return os.getenv("AI_BROLL_ENABLED", "").strip().lower() in _TRUTHY


def _model() -> str:
    return os.getenv("AI_BROLL_MODEL", "").strip() or _DEFAULT_MODEL


def broll_aspect_ratio(block, cast=None) -> str:
    """Aspect ratio for a block's b-roll.

    Bug: this used to always return "9:16" — every layout used to be
    portrait, so that was fine, but once "Horizontal" casts shipped it meant
    AI b-roll was generated portrait and then force-cropped into a 16:9
    canvas (the "horizontal video is heavily zoomed in" report). Now derives
    from the cast's own ``output_format`` when the caller has one — that's
    always the actual shape the clip will be composited into. ``block`` is
    accepted for a future per-block override (e.g. a split-screen layout)
    but isn't read today; kept in the signature so call sites don't need to
    change again when that lands.

    ``AI_BROLL_ASPECT_RATIO`` env override still wins over everything —
    useful for forcing one shape during testing — and the hardcoded "9:16"
    fallback only applies when neither the cast nor the env var says
    anything valid.
    """
    raw = os.getenv("AI_BROLL_ASPECT_RATIO", "").strip()
    if raw in _VALID_ASPECT:
        return raw
    cast_fmt = (getattr(cast, "output_format", None) or "").strip()
    if cast_fmt in _VALID_ASPECT:
        return cast_fmt
    return "9:16"


def _clean_prompt(query: str) -> str:
    q = (query or "").strip()
    if not q:
        raise AiBrollError("no stock_media_query to generate from")
    # Nudge toward usable b-roll: real footage, no text/watermark, gentle motion.
    return (
        f"{q}. Cinematic b-roll footage, realistic, natural lighting, subtle "
        f"camera movement, shallow depth of field. No text, no captions, no watermark."
    )


async def generate_scene_broll_video(
    *,
    prompt_query: str,
    owner_id: str,
    r2=None,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    model_endpoint: str | None = None,
) -> tuple[str, float]:
    """Generate a scene b-roll clip and upload it to R2.

    Returns ``(public_url, cost_usd)``. Raises ``AiBrollError`` on any failure
    (no key, disabled, generation error) — the caller keeps the stock clip.
    """
    if not ai_broll_enabled():
        raise AiBrollError("AI_BROLL_ENABLED is off")

    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise AiBrollError("FAL_API_KEY not configured")

    if r2 is None:
        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()

    if aspect_ratio not in _VALID_ASPECT:
        aspect_ratio = "9:16"
    dur = "10" if int(duration_seconds) >= 10 else "5"
    prompt = _clean_prompt(prompt_query)
    model = (model_endpoint or "").strip() or _model()
    _is_veo = "veo" in model

    try:
        import asyncio
        import fal_client

        def _run():
            os.environ["FAL_KEY"] = app_settings.FAL_API_KEY
            args = {
                "prompt": prompt,
                "duration": dur,
                "aspect_ratio": aspect_ratio,
                "negative_prompt": "text, watermark, logo, caption, distortion, blur",
            }
            if _is_veo:
                # Veo's own audio track is never used — the cast's own TTS
                # voiceover replaces it — and generate_audio defaults to
                # true on fal.ai's side, which costs extra for nothing.
                args["generate_audio"] = False
            return fal_client.subscribe(model, arguments=args)

        result = await asyncio.to_thread(_run)
        video_url = (result or {}).get("video", {}).get("url")
        if not video_url:
            raise AiBrollError(f"model returned no video: {str(result)[:200]}")

        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()
            data = resp.content

        key = f"creators/{owner_id}/casts/broll/ai/{uuid.uuid4().hex[:16]}.mp4"
        await r2.upload_bytes(data, key, "video/mp4")
        public_url = r2.get_public_url(key)
    except AiBrollError:
        raise
    except Exception as e:  # noqa: BLE001 — any fal/network error → stock fallback
        raise AiBrollError(f"scene b-roll generation failed: {str(e)[:200]}") from e

    # Kling 2.5-turbo/pro is priced per second of output (~$0.07/s).
    from services.cost_rates import COST_RATES
    per_s = COST_RATES.get("fal/kling_2.5_turbo_pro", 0.07)
    cost = per_s * (10.0 if dur == "10" else 5.0)
    return public_url, cost
