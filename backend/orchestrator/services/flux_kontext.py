"""FLUX Kontext Pro API wrapper via fal.ai.

Used for cleaning TikTok captions/watermarks from face reference images
and applying user-requested edits while preserving identity.
"""

import json
import logging
import os
from datetime import datetime, timezone

import time

import fal_client
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)


def _log(level: str, service: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": service,
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


def _ensure_fal_key():
    """Ensure FAL_KEY env var is set from app config (fal_client reads it)."""
    if not os.environ.get("FAL_KEY") and settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = settings.FAL_API_KEY


FAL_KONTEXT_MODEL = "fal-ai/flux-pro/kontext"


async def edit_avatar_frame(image_url: str, user_instructions: str = "") -> str:
    """Edit a face reference image using FLUX Kontext Pro.

    Always removes TikTok captions/watermarks/UI elements and preserves identity.
    Optionally applies user-provided instructions on top.

    Args:
        image_url: Public URL of the source image.
        user_instructions: Optional additional editing instructions from the user.

    Returns:
        URL of the edited image from fal.ai response.

    Raises:
        ValueError: If the API returns an error or no image.
    """
    _ensure_fal_key()

    # Build prompt: user instructions are PRIMARY (stated first) so FLUX Kontext
    # doesn't ignore them when the base description is long.
    # IMPORTANT: do NOT mention "watermark", "caption", or "text" — FLUX Kontext
    # sometimes renders those words literally onto the output image.
    if user_instructions:
        # User instruction is primary — stated first and clearly
        prompt = (
            f"{user_instructions}. "
            "Keep the person's face, skin tone, hair, and expression exactly the same. "
            "Clean, high-quality portrait photo result."
        )
    else:
        # No user instruction — just clean the image
        prompt = (
            "Clean portrait photo of the same person. "
            "Erase any superimposed graphic overlays, interface badges, username handles, "
            "and semi-transparent UI stickers layered on top. "
            "The person's face, skin tone, expression, hair, pose, lighting, and background "
            "must remain completely unchanged."
        )

    with sentry_sdk.start_span(op='fal_ai', description='FLUX Kontext edit') as span:
        span.set_data('instructions', (user_instructions or '')[:100])
        _log("info", "flux_kontext", "Sending edit request to FLUX Kontext Pro",
             image_url=image_url[:200], has_user_instructions=bool(user_instructions))

        _fal_start = time.monotonic()
        result = await fal_client.run_async(
            FAL_KONTEXT_MODEL,
            arguments={
                "prompt": prompt,
                "image_url": image_url,
                "guidance_scale": 3.5,
                "num_inference_steps": 28,
                "output_format": "jpeg",
            },
        )

        # Extract edited image URL from response
        images = result.get("images", [])
        if not images:
            _log("error", "flux_kontext", "No images in FLUX Kontext response",
                 response_keys=list(result.keys()))
            raise ValueError("FLUX Kontext Pro returned no images")

        edited_url = images[0].get("url", "")
        if not edited_url:
            raise ValueError("FLUX Kontext Pro returned image entry with no URL")

        span.set_data('status', 'success')
        _log("info", "flux_kontext", "FLUX Kontext edit complete",
             edited_url=edited_url[:100])
        try:
            from services.usage_logger import log_api_usage
            await log_api_usage(
                user_id="", service="flux_kontext", operation="face_edit",
                success=True, duration_seconds=round(time.monotonic() - _fal_start, 1),
                cost_cents=4,  # ~$0.04 per edit
            )
        except Exception:
            pass
        return edited_url
