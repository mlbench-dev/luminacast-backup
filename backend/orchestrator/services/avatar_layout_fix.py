"""Dev-only A/B comparison: two strategies for conforming an avatar's face
reference photo to a cast's layout.

Root cause this exists to let us compare fixes for: ``Avatar.face_ref_key``
is generated with no aspect-ratio constraint anywhere in the avatar-creation
paths (AI Avatar wizard, legacy Gemini path, manual upload all skip any
size/aspect parameter) — a confirmed real case came back 1024x768 (landscape)
for a cast using a 1080x1920 (portrait) canvas, which the render pipeline's
existing conform step (``services/aspect_conform.py``) correctly detects as
a severe mismatch and letterboxes with a blurred pad rather than over-crop.
That decision isn't wrong, but the result doesn't look good, and the real
fix is to stop the source from being this mismatched in the first place.

Two candidate fixes, not yet decided between (see
``routers/dev_avatar_layout_fix.py`` for the comparison tool that uses
these):

  - ``crop_face_ref_to_format`` — free, instant, but can cut off real
    content on a severe mismatch.
  - ``generate_face_ref_for_format`` — AI-generated (Nano Banana Pro
    outpaint), preserves all content, costs money + latency.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Target sizes per format — same values already established in
# tasks/avatar_looks.py's _FLUX_KONTEXT_IMAGE_SIZE_BY_ORIENTATION for the
# earlier action-frame orientation fix, kept in sync deliberately.
_TARGET_SIZE_BY_FORMAT = {
    "vertical": {"width": 1024, "height": 1792},
    "horizontal": {"width": 1792, "height": 1024},
}

_ASPECT_RATIO_BY_FORMAT = {
    "vertical": "9:16",
    "horizontal": "16:9",
}


def _target_size(format_family: str) -> dict:
    return _TARGET_SIZE_BY_FORMAT.get(format_family, _TARGET_SIZE_BY_FORMAT["vertical"])


def crop_face_ref_to_format(image_bytes: bytes, format_family: str) -> bytes:
    """Strategy 1: deterministic center-crop, no content generation.

    Reuses ``services.aspect_conform.conform_image_bytes`` with
    ``threshold=0.0`` — this forces its cover-crop branch and disables the
    contain+blur branch entirely, the same trick
    ``services/block_normalize.py``'s ``force_cover`` path already uses for
    PIP blocks (chosen there after a past regression where contain+blur made
    a PIP avatar "a washed-out/near-invisible sliver"). Fast, free, but a
    severe mismatch means real content (e.g. the sides, on a wide source
    going into a tall target) gets cropped away.
    """
    from services.aspect_conform import conform_image_bytes

    size = _target_size(format_family)
    return conform_image_bytes(
        image_bytes, size["width"], size["height"], threshold=0.0,
    )


async def generate_face_ref_for_format(image_url: str, format_family: str) -> bytes:
    """Strategy 2: AI-generate a layout-conformed version (Nano Banana Pro).

    Mirrors the NBP call already used in tasks/avatar_looks.py's
    look-generation branch (same model/call shape, same identity-lock
    framing) — reused here directly rather than gated behind
    ``nano_banana_pro_enabled()``, since this dev tool exists specifically
    to see what NBP produces, regardless of whether that flag is on for the
    production avatar-look paths.
    """
    import httpx

    from services.nano_banana import edit_image_run_async

    aspect_ratio = _ASPECT_RATIO_BY_FORMAT.get(format_family, "9:16")
    prompt = (
        "Extend this photo to fill the full frame in the requested aspect "
        "ratio. Keep the same person, same face, same identity, same "
        "outfit, same lighting and background style — extend/outpaint the "
        "scene naturally beyond the original edges rather than cropping "
        "anything out or distorting proportions. Photorealistic, seamless "
        "extension, no visible seams."
    )
    result_url = await edit_image_run_async(
        prompt, [image_url], aspect_ratio=aspect_ratio,
    )

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(result_url)
        resp.raise_for_status()
        return resp.content
