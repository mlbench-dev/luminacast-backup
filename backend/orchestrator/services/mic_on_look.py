"""Step 8 — Mic-on avatar look-variant (baked clip-on via FLUX).

When a block's mic is ON the avatar should physically wear a small clip-on
lavalier microphone on the collar/lapel, consistent across lipsync and
body-motion blocks. When mic is OFF the clean (base) look is used. When the
per-block ``mic_on`` flag is NULL the renderer keeps its current default
behaviour.

This module owns:

  * ``MIC_ON_LOOK_PROMPT`` — the single, auditable FLUX Kontext prompt that
    adds the clip-on lavalier while keeping the rest of the look identical.
  * ``generate_mic_on_variant`` — idempotent generator that derives a
    ``look_type='mic_on_<base_look_type>'`` ``AvatarLook`` from an existing
    base look's ``face_ref_key`` and caches it per (avatar, base_look) pair.
  * ``resolve_mic_on_face_key`` — the renderer-facing entry point that swaps a
    resolved base ``face_ref_key`` for the mic-on variant when a block has
    ``mic_on == True`` (lazy-generating the variant on first use), returns the
    base key when ``mic_on == False``, and preserves the default when
    ``mic_on`` is None.

Behind feature flag ``MIC_ON_LOOK_VARIANT_ENABLED`` (default ``true``). When
the flag is OFF the resolver always returns the base key, so the renderer
behaves exactly as it did before Step 8.

Failure policy (RULES.md / global rules): every ``except`` reports to Sentry,
logs a warning, and falls back to the base look — the clip-on overlay matters
less than the render completing, so a FLUX failure must never block a render.

No engine/provider name appears in any user-facing string. ``flux`` in logs is
fine; this module produces no user-visible toast/label/response text.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)


# Single, auditable prompt. Vetted constraints: clip-on lavalier on the
# collar/lapel only; explicitly NO handheld, headset, or boom mic; keep
# everything else (face, hair, expression, lighting, clothing, background)
# identical so the avatar's identity and look are preserved.
MIC_ON_LOOK_PROMPT = (
    "Add a small, subtle, realistic clip-on lavalier microphone on the "
    "subject's collar or lapel. Keep face, hair, expression, lighting, "
    "clothing, and background exactly the same. The mic should look "
    "professional and broadcast-quality. Do NOT add a handheld microphone, "
    "headset mic, or boom mic."
)

# look_type prefix used for every mic-on variant row.
MIC_ON_LOOK_TYPE_PREFIX = "mic_on_"

# FLUX Kontext image-to-image guidance. Env-overridable; no hardcoded magic
# that can't be tuned in production without a redeploy.
_DEFAULT_GUIDANCE_SCALE = 3.5
_DEFAULT_INFERENCE_STEPS = 28


def _log(level: str, message: str, **kwargs) -> None:
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": "mic_on_look",
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


def mic_on_look_enabled() -> bool:
    """Feature flag gate. Default ON; env-overridable.

    Flip ``MIC_ON_LOOK_VARIANT_ENABLED=false`` to disable the baked clip-on
    entirely — the resolver then always returns the base look key, so the
    renderer keeps its pre-Step-8 behaviour for every block.
    """
    return os.environ.get("MIC_ON_LOOK_VARIANT_ENABLED", "true").strip().lower() == "true"


def _guidance_scale() -> float:
    try:
        return float(os.environ.get("MIC_ON_LOOK_GUIDANCE_SCALE", _DEFAULT_GUIDANCE_SCALE))
    except (TypeError, ValueError):
        return _DEFAULT_GUIDANCE_SCALE


def _inference_steps() -> int:
    try:
        return int(os.environ.get("MIC_ON_LOOK_INFERENCE_STEPS", _DEFAULT_INFERENCE_STEPS))
    except (TypeError, ValueError):
        return _DEFAULT_INFERENCE_STEPS


def _download_timeout() -> float:
    try:
        return float(os.environ.get("MIC_ON_LOOK_HTTP_TIMEOUT", "60"))
    except (TypeError, ValueError):
        return 60.0


def _cache_name(base_look_id: str) -> str:
    """Stable ``name`` value that pins a mic-on variant to its base look.

    The (avatar, base_look) pair is the cache key. ``look_type`` carries the
    spec-required ``mic_on_<base_type>`` value (which is NOT unique per base
    look when an avatar has several looks of the same type), so the precise
    pairing is encoded here and matched on lookup for idempotency.
    """
    return f"mic-on:{base_look_id}"


async def _existing_variant(session, avatar_id: str, base_look_id: str):
    """Return a ready/in-flight mic-on variant for this (avatar, base_look)
    pair, or None. A ready row is reused as-is; a non-failed row that is still
    generating is also returned so concurrent renders don't double-generate.
    """
    from models.avatar_look import AvatarLook
    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(AvatarLook)
        .where(AvatarLook.avatar_id == avatar_id)
        .where(AvatarLook.name == _cache_name(base_look_id))
        .where(AvatarLook.look_type.like(f"{MIC_ON_LOOK_TYPE_PREFIX}%"))
        .where(AvatarLook.status != "failed")
        .order_by(AvatarLook.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _run_flux_clip_on(face_url: str) -> str:
    """Run FLUX Kontext to bake the clip-on lavalier onto the base face image.

    Isolated in its own coroutine so tests can mock the network call in one
    place. Returns the output image URL. Raises on any API/transport error so
    the caller's fallback path engages.
    """
    import asyncio
    import fal_client
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    guidance = _guidance_scale()
    steps = _inference_steps()

    def _call():
        source_url = fal_client.upload_file_from_url(face_url) if hasattr(
            fal_client, "upload_file_from_url"
        ) else face_url
        result = fal_client.subscribe(
            "fal-ai/flux-pro/kontext",
            arguments={
                "image_url": source_url,
                "prompt": MIC_ON_LOOK_PROMPT,
                "guidance_scale": guidance,
                "num_inference_steps": steps,
                "output_format": "jpeg",
            },
        )
        return result

    result = await asyncio.to_thread(_call)

    output_image_url = None
    if isinstance(result, dict):
        images = result.get("images") or []
        if images and isinstance(images[0], dict):
            output_image_url = images[0].get("url")
        elif "image" in result:
            img = result["image"]
            output_image_url = img.get("url") if isinstance(img, dict) else img
    if not output_image_url:
        raise RuntimeError(f"FLUX Kontext returned no output image: {str(result)[:300]}")
    return output_image_url


async def generate_mic_on_variant(avatar_id: str, base_look_id: str, session):
    """Idempotently produce a mic-on look variant for ``base_look_id``.

    Takes the base look's ``face_ref_key`` (R2 key) as the FLUX source, bakes
    a subtle clip-on lavalier via :data:`MIC_ON_LOOK_PROMPT`, uploads the
    result to R2, and persists a new ``avatar_looks`` row with
    ``look_type='mic_on_<base_look_type>'`` pointing at the new image.

    Idempotent: if a mic-on variant already exists for the (avatar, base_look)
    pair it is returned without regenerating (logs a cache hit).

    Returns the ready :class:`AvatarLook` on success, or ``None`` on failure
    (so callers fall back to the base look). Never raises.
    """
    from models.avatar_look import AvatarLook
    from services.r2_storage import get_r2_storage_service

    try:
        cached = await _existing_variant(session, avatar_id, base_look_id)
        if cached is not None and cached.status == "ready" and cached.face_ref_key:
            _log(
                "info",
                f"mic-on look cache hit avatar={avatar_id} base_look={base_look_id} "
                f"mic_on_look={cached.id}",
                avatar_id=avatar_id,
                base_look_id=base_look_id,
                mic_on_look_id=cached.id,
            )
            return cached
        if cached is not None and cached.status == "generating":
            # Another render is already baking this variant. Don't double-call
            # FLUX; the caller falls back to the base look for this render.
            _log(
                "info",
                f"mic-on look generation already in flight avatar={avatar_id} "
                f"base_look={base_look_id} mic_on_look={cached.id}",
                avatar_id=avatar_id,
                base_look_id=base_look_id,
                mic_on_look_id=cached.id,
            )
            return None

        base_look = await session.get(AvatarLook, base_look_id)
        if base_look is None or not base_look.face_ref_key:
            _log(
                "warning",
                f"mic-on look skipped: base look missing face_ref_key avatar={avatar_id} "
                f"base_look={base_look_id}",
                avatar_id=avatar_id,
                base_look_id=base_look_id,
            )
            return None

        base_type = base_look.look_type or "background"
        look_type_value = f"{MIC_ON_LOOK_TYPE_PREFIX}{base_type}"

        variant = AvatarLook(
            id=f"al_{uuid.uuid4().hex[:12]}",
            avatar_id=avatar_id,
            name=_cache_name(base_look_id),
            is_default=False,
            is_original=False,
            status="generating",
            look_type=look_type_value,
        )
        session.add(variant)
        await session.commit()
        await session.refresh(variant)

        r2 = get_r2_storage_service()
        face_url = r2.get_public_url(base_look.face_ref_key)
        _log(
            "info",
            f"mic-on look generating via flux avatar={avatar_id} base_look={base_look_id} "
            f"mic_on_look={variant.id}",
            avatar_id=avatar_id,
            base_look_id=base_look_id,
            mic_on_look_id=variant.id,
        )

        output_image_url = await _run_flux_clip_on(face_url)

        import httpx

        tmpdir = tempfile.mkdtemp(prefix=f"mic_on_{variant.id}_")
        try:
            output_path = os.path.join(tmpdir, "mic_on.jpg")
            async with httpx.AsyncClient(timeout=_download_timeout()) as client:
                resp = await client.get(output_image_url)
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    f.write(resp.content)

            look_r2_key = (
                f"creators/{base_look.avatar_id}/avatars/{avatar_id}/looks/{variant.id}.jpg"
            )
            await r2.upload_file(output_path, look_r2_key, content_type="image/jpeg")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        variant.face_ref_key = look_r2_key
        variant.status = "ready"
        await session.commit()
        await session.refresh(variant)

        _log(
            "info",
            f"mic-on look ready avatar={avatar_id} base_look={base_look_id} "
            f"mic_on_look={variant.id}",
            avatar_id=avatar_id,
            base_look_id=base_look_id,
            mic_on_look_id=variant.id,
            face_ref_key=look_r2_key,
        )
        return variant
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log(
            "warning",
            f"mic-on look generation failed avatar={avatar_id} base_look={base_look_id}: {e}",
            avatar_id=avatar_id,
            base_look_id=base_look_id,
        )
        # Best-effort: mark the in-flight row failed so a later render retries
        # instead of being blocked by a stuck 'generating' row.
        try:
            if "variant" in locals() and variant is not None:
                variant.status = "failed"
                variant.error_message = str(e)[:500]
                await session.commit()
        except Exception as e2:
            sentry_sdk.capture_exception(e2)
        return None


async def resolve_mic_on_face_key(
    mic_on: Optional[bool],
    avatar_id: Optional[str],
    base_look_id: Optional[str],
    base_face_key: str,
    session,
) -> str:
    """Renderer entry point: map a resolved base look to the right face key.

    - ``mic_on is True``  → the mic-on variant's ``face_ref_key`` (lazy-generated
      and cached on first use). Falls back to ``base_face_key`` if generation
      fails or no usable base look id is available.
    - ``mic_on is False`` → ``base_face_key`` (the clean look).
    - ``mic_on is None``  → ``base_face_key`` (preserve default behaviour).

    Also returns ``base_face_key`` unchanged when the feature flag is OFF.
    Never raises — a failure to bake the clip-on must never block a render.
    """
    if not base_face_key:
        return base_face_key
    if mic_on is not True:
        return base_face_key
    if not mic_on_look_enabled():
        return base_face_key
    if not avatar_id or not base_look_id:
        # No concrete base look to derive from (e.g. legacy avatar.face_ref_key
        # fallback). Keep the clean look rather than guessing.
        return base_face_key

    try:
        variant = await generate_mic_on_variant(avatar_id, base_look_id, session)
        if variant is not None and variant.face_ref_key:
            return variant.face_ref_key
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log(
            "warning",
            f"mic-on resolve fell back to base look avatar={avatar_id} base_look={base_look_id}: {e}",
            avatar_id=avatar_id,
            base_look_id=base_look_id,
        )
    return base_face_key
