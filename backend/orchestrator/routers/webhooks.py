"""RunPod webhook receiver.

RunPod POSTs job results here when a serverless job completes (success or failure).
This replaces the polling loop in services/runpod.py for cast generation.

Security: RunPod sends the job result as JSON. We verify the job_id exists in our
variant records. No secret token needed because we match job_id to our DB records.
"""

import base64
import logging
import json
from datetime import datetime, timezone

import sentry_sdk
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import async_session_factory

logger = logging.getLogger(__name__)


from services.runpod_error_translate import normalize_error_message as _normalize_error_message  # noqa: E402

async def _apply_product_overlays_to_variant(
    db,
    variant,
    block,
    cast,
    avatar_fit: str,
) -> list:
    """Apply all product overlays for this variant's block.

    Returns a list of warning dicts for any failures.
    Mutates variant.video_key on success.
    """
    from models.product import Product
    from models.cast import CastProduct
    from sqlalchemy import select
    from services.video_compositor import (
        ensure_product_cover_on_r2,
        composite_product_overlay,
    )

    warnings = []

    # Products linked to this cast — used to assert any resolved overlay asset
    # belongs to one of the cast's products (Phase 4 belongs-to-cast guard).
    cast_linked_product_ids = []
    if cast is not None:
        rows = await db.execute(
            select(CastProduct.product_id).where(CastProduct.cast_id == cast.id)
        )
        cast_linked_product_ids = [r[0] for r in rows.all()]
    effects = cast.effects_config or {}
    scene_objects = list(effects.get("scene_objects", []))

    # NOTE: the two legacy auto-synthesis fallbacks that used to live here
    # (build a "product" scene_object from `product_overlay.enabled`, and —
    # separately — synthesize one from any block.product_id with no explicit
    # scene_objects at all) were removed per client request: they always
    # defaulted to x=0.7/y=0.7 (bottom-right), burning a small product image
    # onto every product-attached block whether or not anyone asked for it.
    # Nothing in the current editor UI ever writes `scene_objects` or
    # `product_overlay`, so this only ever fired the bottom-right default.
    # Only genuinely explicit `scene_objects` entries (still supported, for
    # any cast that has them stored) produce an overlay now.

    # Filter to products that apply to this block
    product_objs = [
        o for o in scene_objects
        if o.get("kind") == "product"
        and (not o.get("block_ids") or block.id in o.get("block_ids", []))
    ]

    for pobj in product_objs:
        pid = (pobj.get("product") or {}).get("product_id") or (block.product_id if block else None)
        if not pid:
            continue

        product = await db.get(Product, pid)
        if not product:
            warnings.append({"step": "product_overlay", "error": f"Product {pid} not found"})
            continue

        cover_key = await ensure_product_cover_on_r2(
            product,
            db=db,
            block_id=(block.id if block else ""),
            cast_linked_product_ids=cast_linked_product_ids,
        )
        if not cover_key:
            logger.error(
                "Cannot composite product %s onto variant %s: no cover image resolvable",
                product.id, variant.id,
            )
            warnings.append({
                "step": "product_overlay",
                "error": f"No cover image for product {pid} ({product.name})",
            })
            continue

        # Map normalized x/y to position preset
        obj_x = pobj.get("x", 0.7)
        obj_y = pobj.get("y", 0.7)
        obj_size = pobj.get("width", 0.25)

        if obj_x < 0.35 and obj_y < 0.35:
            pos_preset = "top_left"
        elif obj_x >= 0.65 and obj_y < 0.35:
            pos_preset = "top_right"
        elif obj_x < 0.35 and obj_y >= 0.65:
            pos_preset = "bottom_left"
        elif obj_x >= 0.65 and obj_y >= 0.65:
            pos_preset = "bottom_right"
        elif obj_y < 0.35:
            pos_preset = "top_center"
        elif obj_y >= 0.65:
            pos_preset = "bottom_center"
        else:
            pos_preset = "center"

        try:
            composited_key = variant.video_key.replace(".mp4", "_composited.mp4")
            await composite_product_overlay(
                avatar_video_url=f"https://media.luminacast.com/{variant.video_key}",
                product_image_url=f"https://media.luminacast.com/{cover_key}",
                output_key=composited_key,
                layout=pos_preset,
                product_size=obj_size,
                avatar_fit_mode=avatar_fit,
            )
            variant.video_key = composited_key
            logger.info(
                "Product overlay applied: variant=%s product=%s pos=%s key=%s",
                variant.id, pid, pos_preset, composited_key,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(
                "Product overlay failed for variant %s, product %s: %s",
                variant.id, pid, e, exc_info=True,
            )
            warnings.append({
                "step": "product_overlay",
                "error": f"Compositor error for {product.name}: {str(e)[:200]}",
            })

    return warnings


router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/runpod/infinitetalk")
async def runpod_infinitetalk_webhook(request: Request):
    """Receive InfiniteTalk job completion from RunPod.

    RunPod sends:
    {
        "id": "job_abc123",
        "status": "COMPLETED" | "FAILED" | "TIMED_OUT",
        "output": { ... model output ... },
        "error": "..." (if failed),
        "executionTime": 123456  (ms)
    }
    """
    payload = await request.json()

    job_id = payload.get("id", "")
    status = payload.get("status", "")
    output = payload.get("output", {})
    error = payload.get("error", "")
    exec_time_ms = payload.get("executionTime", 0)

    logger.info(json.dumps({
        "service": "webhook",
        "event": "runpod_infinitetalk_result",
        "job_id": job_id,
        "status": status,
        "exec_time_s": round(exec_time_ms / 1000, 1) if exec_time_ms else 0,
        "has_output": bool(output),
        "error": _normalize_error_message(error[:200]) if error else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    if not job_id:
        raise HTTPException(400, "Missing job ID")

    from models.variant import Variant, VariantStatus
    from models.cast import Cast
    from models.block import Block
    from config import settings

    async with async_session_factory() as db:
        # Find the variant by runpod_job_id
        variant = await db.scalar(
            select(Variant).where(Variant.runpod_job_id == job_id)
        )

        if not variant:
            # Check if this is an avatar test video webhook
            from models.avatar import Avatar, AvatarStatus, AvatarPhase
            avatar = await db.scalar(
                select(Avatar).where(Avatar.runpod_job_id == job_id)
            )
            if avatar:
                return await _handle_avatar_test_video_webhook(db, avatar, job_id, status, output, error, exec_time_ms)

            logger.warning(f"Webhook received for unknown job_id: {job_id}")
            return {"status": "ignored", "reason": "unknown job_id"}

        block = await db.get(Block, variant.block_id)
        cast = await db.get(Cast, block.cast_id) if block else None

        if status == "COMPLETED":
            # InfiniteTalk returns base64-encoded MP4 in output.video
            video_b64 = output.get("video", "") if isinstance(output, dict) else ""

            # Fallback: check for URL-based output formats
            video_url = ""
            if isinstance(output, dict) and not video_b64:
                video_url = (
                    output.get("video_url")
                    or output.get("video_r2_key")
                    or output.get("result", {}).get("video_url")
                    if isinstance(output.get("result"), dict) else ""
                ) or ""

            if video_b64:
                # Decode base64 video and upload to R2
                video_bytes = base64.b64decode(video_b64)
                video_key = f"creators/{cast.user_id}/casts/{cast.id}/clips/{variant.id}.mp4"

                from services.r2_storage import get_r2_storage_service, IMMUTABLE_CACHE_CONTROL
                r2 = get_r2_storage_service()
                await r2.upload_bytes(video_bytes, video_key, content_type="video/mp4", cache_control=IMMUTABLE_CACHE_CONTROL)

                variant.video_key = video_key
                variant.status = VariantStatus.READY
                variant.generation_error = None
                variant.duration_seconds = output.get("duration_seconds", 0) if isinstance(output, dict) else 0

                logger.info(f"Variant {variant.id} ready (base64→R2): {video_key} ({len(video_bytes)} bytes, {exec_time_ms / 1000:.0f}s)")

                # Post-processing Step 1: Background replacement (Path B — video/blur/animated)
                if cast:
                    bg_config = (cast.effects_config or {}).get("background", {})
                    bg_type = bg_config.get("type", "original")
                    # Path B only handles post-InfiniteTalk types (video, blur)
                    # Static types (color, image, gradient) are pre-composited in cast_generator
                    if bg_type in ("video", "blur"):
                        try:
                            from services.background_compositor import replace_background
                            bg_key = variant.video_key.replace(".mp4", "_bg.mp4")
                            bg_result = await replace_background(
                                avatar_video_url=f"https://media.luminacast.com/{variant.video_key}",
                                output_key=bg_key,
                                bg_type=bg_type,
                                bg_color=bg_config.get("color", "#1a1a2e"),
                                bg_image_url=f"https://media.luminacast.com/{bg_config['image_key']}" if bg_config.get("image_key") else "",
                                bg_video_url=f"https://media.luminacast.com/{bg_config['video_key']}" if bg_config.get("video_key") else "",
                                blur_strength=bg_config.get("blur_strength", 20),
                                gradient=bg_config.get("gradient"),
                                animation=bg_config.get("animation", "none"),
                                ken_burns_speed=bg_config.get("ken_burns_speed", 0.03),
                            )
                            if not bg_result.get("skipped"):
                                variant.video_key = bg_key
                                logger.info(f"Background replaced ({bg_type}): {bg_key}")
                        except Exception as bg_err:
                            sentry_sdk.capture_exception(bg_err)
                            logger.warning(f"Background replacement failed, keeping original: {bg_err}")

                # Post-processing Steps 2-4: Read from unified scene_objects or legacy format
                if cast:
                    effects = cast.effects_config or {}
                    scene_objects = effects.get("scene_objects", [])
                    avatar_fit = effects.get("avatar_fit", {}).get("mode", "cover")

                    # Migrate legacy: if no scene_objects, carry over any real
                    # text/sticker overlays. The old `product_overlay.enabled`
                    # auto-injection (always bottom-right, x=0.7/y=0.7) was
                    # removed per client request — see
                    # _apply_product_overlays_to_variant for the full note.
                    if not scene_objects:
                        for ov in effects.get("overlays", []):
                            scene_objects.append(ov)

                    # Filter objects for this block
                    block_objects = [
                        o for o in scene_objects
                        if not o.get("block_ids") or block.id in o.get("block_ids", [])
                    ]

                    # Step 2: Product overlays
                    prod_warnings = await _apply_product_overlays_to_variant(
                        db, variant, block, cast, avatar_fit,
                    )
                    if prod_warnings:
                        existing = list(variant.composition_warnings or [])
                        variant.composition_warnings = existing + prod_warnings

                    # Step 3: Text/sticker overlays
                    text_objs = [o for o in block_objects if o.get("kind") in ("text", "sticker")]
                    # Convert scene_objects format to overlay format for compositor
                    visual_overlays = []
                    for tobj in text_objs:
                        ov = {
                            "kind": tobj.get("kind"),
                            "x": tobj.get("x", 0.5),
                            "y": tobj.get("y", 0.5),
                            "start_ms": tobj.get("start_ms", 0),
                            "duration_ms": tobj.get("duration_ms", 3000),
                        }
                        if tobj.get("kind") == "text" and tobj.get("text"):
                            text_val = tobj["text"]
                            if isinstance(text_val, dict):
                                ov["text"] = text_val.get("content", "")
                                style_id = text_val.get("style", "bold_pop")
                            else:
                                ov["text"] = str(text_val)
                                style_id = "bold_pop"
                            ov["font_size_pct"] = tobj.get("font_size_pct", 0.06)
                            ov["color"] = tobj.get("color", "white")
                            ov["stroke"] = tobj.get("stroke", "black")
                        elif tobj.get("kind") == "sticker" and tobj.get("sticker"):
                            sticker_val = tobj["sticker"]
                            if isinstance(sticker_val, dict):
                                ov["sticker_emoji"] = sticker_val.get("emoji", "⭐")
                            else:
                                ov["sticker_emoji"] = str(sticker_val)
                        visual_overlays.append(ov)

                    # Also include legacy overlays
                    legacy_overlays = [o for o in effects.get("overlays", []) if o.get("kind") in ("text", "sticker")]
                    all_visual = visual_overlays + [o for o in legacy_overlays if not o.get("block_ids") or block.id in o.get("block_ids", [])]

                    if all_visual:
                        try:
                            from services.video_compositor import composite_text_overlays
                            txt_key = variant.video_key.replace(".mp4", "_txt.mp4")
                            txt_result = await composite_text_overlays(
                                video_url=f"https://media.luminacast.com/{variant.video_key}",
                                output_key=txt_key,
                                overlays=all_visual,
                                video_duration=variant.duration_seconds or 0,
                            )
                            if not txt_result.get("skipped"):
                                variant.video_key = txt_key
                                logger.info(f"Text overlays applied ({len(all_visual)}): {txt_key}")
                        except Exception as txt_err:
                            sentry_sdk.capture_exception(txt_err)
                            logger.warning(f"Text overlay failed, keeping previous video: {txt_err}")

                    # Step 4: SFX audio mix
                    sfx_objs = [o for o in block_objects if o.get("kind") == "sfx"]
                    sfx_overlays = []
                    for sobj in sfx_objs:
                        sfx_ov = {
                            "kind": "sfx",
                            "sfx_preset": (sobj.get("sfx") or {}).get("preset", "ping"),
                            "start_ms": sobj.get("start_ms", 0),
                            "sfx_volume": (sobj.get("sfx") or {}).get("volume", 0.8),
                        }
                        sfx_overlays.append(sfx_ov)
                    # Also include legacy sfx
                    legacy_sfx = [o for o in effects.get("overlays", []) if o.get("kind") == "sfx"]
                    all_sfx = sfx_overlays + [o for o in legacy_sfx if not o.get("block_ids") or block.id in o.get("block_ids", [])]

                    if all_sfx:
                        try:
                            from services.video_compositor import composite_sfx_mix
                            sfx_key = variant.video_key.replace(".mp4", "_sfx.mp4")
                            sfx_result = await composite_sfx_mix(
                                video_url=f"https://media.luminacast.com/{variant.video_key}",
                                output_key=sfx_key,
                                sfx_overlays=all_sfx,
                                video_duration=variant.duration_seconds or 0,
                            )
                            if not sfx_result.get("skipped"):
                                variant.video_key = sfx_key
                                logger.info(f"SFX mixed ({len(all_sfx)}): {sfx_key}")
                        except Exception as sfx_err:
                            sentry_sdk.capture_exception(sfx_err)
                            logger.warning(f"SFX mix failed, keeping previous video: {sfx_err}")

            elif video_url:
                # Extract R2 key from URL if it's a full URL
                video_key = video_url
                if video_key.startswith("http"):
                    video_key = (
                        video_key
                        .replace(settings.R2_PUBLIC_URL + "/", "")
                        .replace("https://media.luminacast.com/", "")
                    )

                variant.video_key = video_key
                variant.status = VariantStatus.READY
                variant.generation_error = None
                variant.duration_seconds = output.get("duration_seconds", 0) if isinstance(output, dict) else 0

                logger.info(f"Variant {variant.id} ready: {video_key} ({exec_time_ms / 1000:.0f}s)")

                # Post-processing Step 1: Background replacement (Path B)
                if cast:
                    bg_cfg = (cast.effects_config or {}).get("background", {})
                    bg_t = bg_cfg.get("type", "original")
                    if bg_t in ("video", "blur"):
                        try:
                            from services.background_compositor import replace_background as replace_bg
                            bg_k = variant.video_key.replace(".mp4", "_bg.mp4")
                            bg_r = await replace_bg(
                                avatar_video_url=f"https://media.luminacast.com/{variant.video_key}",
                                output_key=bg_k,
                                bg_type=bg_t,
                                bg_color=bg_cfg.get("color", "#1a1a2e"),
                                bg_image_url=f"https://media.luminacast.com/{bg_cfg['image_key']}" if bg_cfg.get("image_key") else "",
                                bg_video_url=f"https://media.luminacast.com/{bg_cfg['video_key']}" if bg_cfg.get("video_key") else "",
                                blur_strength=bg_cfg.get("blur_strength", 20),
                                gradient=bg_cfg.get("gradient"),
                                animation=bg_cfg.get("animation", "none"),
                            )
                            if not bg_r.get("skipped"):
                                variant.video_key = bg_k
                                logger.info(f"Background replaced (URL path, {bg_t}): {bg_k}")
                        except Exception as bg_e:
                            sentry_sdk.capture_exception(bg_e)
                            logger.warning(f"Background replacement failed (URL path): {bg_e}")

                # Post-processing Steps 2-4 (URL path): unified scene_objects
                if cast:
                    eff_url = cast.effects_config or {}
                    scene_objs_url = eff_url.get("scene_objects", [])
                    avatar_fit_url = eff_url.get("avatar_fit", {}).get("mode", "cover")

                    # Legacy migration: carry over any real text/sticker
                    # overlays only. The old `product_overlay.enabled`
                    # auto-injection (always bottom-right) was removed per
                    # client request — see _apply_product_overlays_to_variant.
                    if not scene_objs_url:
                        for ov in eff_url.get("overlays", []):
                            scene_objs_url.append(ov)

                    block_objs_url = [o for o in scene_objs_url if not o.get("block_ids") or block.id in o.get("block_ids", [])]

                    # Step 2: Product overlays (URL path)
                    prod_warnings_url = await _apply_product_overlays_to_variant(
                        db, variant, block, cast, avatar_fit_url,
                    )
                    if prod_warnings_url:
                        existing = list(variant.composition_warnings or [])
                        variant.composition_warnings = existing + prod_warnings_url

                    # Step 3: Text/sticker overlays (URL path)
                    txt_objs_url = [o for o in block_objs_url if o.get("kind") in ("text", "sticker")]
                    vis_ovs = []
                    for tobj in txt_objs_url:
                        ov = {"kind": tobj.get("kind"), "x": tobj.get("x", 0.5), "y": tobj.get("y", 0.5), "start_ms": tobj.get("start_ms", 0), "duration_ms": tobj.get("duration_ms", 3000)}
                        if tobj.get("kind") == "text" and tobj.get("text"):
                            ov["text"] = tobj["text"].get("content", "")
                            ov["font_size_pct"] = 0.06
                            ov["color"] = "white"
                            ov["stroke"] = "black"
                        elif tobj.get("kind") == "sticker" and tobj.get("sticker"):
                            sticker_val = tobj["sticker"]
                            if isinstance(sticker_val, dict):
                                ov["sticker_emoji"] = sticker_val.get("emoji", "⭐")
                            else:
                                ov["sticker_emoji"] = str(sticker_val)
                        vis_ovs.append(ov)
                    legacy_txt = [o for o in eff_url.get("overlays", []) if o.get("kind") in ("text", "sticker") and (not o.get("block_ids") or block.id in o.get("block_ids", []))]
                    all_vis_url = vis_ovs + legacy_txt
                    if all_vis_url:
                        try:
                            from services.video_compositor import composite_text_overlays as comp_txt
                            tk = variant.video_key.replace(".mp4", "_txt.mp4")
                            tr = await comp_txt(video_url=f"https://media.luminacast.com/{variant.video_key}", output_key=tk, overlays=all_vis_url, video_duration=variant.duration_seconds or 0)
                            if not tr.get("skipped"):
                                variant.video_key = tk
                                logger.info(f"Text overlays applied (URL path, {len(all_vis_url)}): {tk}")
                        except Exception as te:
                            sentry_sdk.capture_exception(te)
                            logger.warning(f"Text overlay failed (URL path): {te}")

                    # Step 4: SFX audio mix (URL path)
                    sfx_objs_url = [o for o in block_objs_url if o.get("kind") == "sfx"]
                    sfx_ovs = [{"kind": "sfx", "sfx_preset": (s.get("sfx") or {}).get("preset", "ping"), "start_ms": s.get("start_ms", 0), "sfx_volume": (s.get("sfx") or {}).get("volume", 0.8)} for s in sfx_objs_url]
                    legacy_sfx_url = [o for o in eff_url.get("overlays", []) if o.get("kind") == "sfx" and (not o.get("block_ids") or block.id in o.get("block_ids", []))]
                    all_sfx_url = sfx_ovs + legacy_sfx_url
                    if all_sfx_url:
                        try:
                            from services.video_compositor import composite_sfx_mix as sfx_mix
                            sk = variant.video_key.replace(".mp4", "_sfx.mp4")
                            sr = await sfx_mix(video_url=f"https://media.luminacast.com/{variant.video_key}", output_key=sk, sfx_overlays=all_sfx_url, video_duration=variant.duration_seconds or 0)
                            if not sr.get("skipped"):
                                variant.video_key = sk
                                logger.info(f"SFX mixed (URL path, {len(all_sfx_url)}): {sk}")
                        except Exception as se:
                            sentry_sdk.capture_exception(se)
                            logger.warning(f"SFX mix failed (URL path): {se}")

            else:
                variant.status = VariantStatus.FAILED
                variant.generation_error = "Lip Sync Engine completed but returned no video URL or video data"
                logger.error(f"Variant {variant.id}: completed but no video in output keys: {list(output.keys()) if isinstance(output, dict) else type(output)}")

        elif status in ("FAILED", "TIMED_OUT"):
            variant.status = VariantStatus.FAILED
            error_msg = error
            if not error_msg and isinstance(output, dict):
                error_msg = output.get("error") or output.get("message") or ""
            if not error_msg:
                error_msg = f"RunPod job {status}"
            variant.generation_error = _normalize_error_message(str(error_msg)[:500])
            logger.error(f"Variant {variant.id} failed: {variant.generation_error}")

        else:
            logger.warning(f"Unexpected webhook status for {job_id}: {status}")
            return {"status": "ignored", "reason": f"unexpected status: {status}"}

        await db.commit()

        # Check if ALL variants for this cast are now done (ready or failed)
        if cast:
            await _check_cast_completion(db, cast)

        # Log API usage
        try:
            from services.usage_logger import log_api_usage
            await log_api_usage(
                user_id=cast.user_id if cast else "",
                service="runpod",
                operation="infinitetalk_webhook",
                cast_id=cast.id if cast else None,
                duration_seconds=exec_time_ms / 1000 if exec_time_ms else None,
                cost_cents=max(1, int((exec_time_ms / 1000 / 3600) * 76)) if exec_time_ms else 0,
                success=(status == "COMPLETED"),
                runpod_job_id=job_id,
                error_message=_normalize_error_message(error[:500]) if error else None,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Failed to log API usage: {e}")

    return {"status": "processed", "variant_id": variant.id, "result": status}


async def _handle_avatar_test_video_webhook(db, avatar, job_id, status, output, error, exec_time_ms):
    """Process RunPod webhook for avatar test video rendering.

    Called when the job_id matches an avatar.runpod_job_id instead of a variant.
    Downloads the rendered video, uploads to R2, and marks the avatar as READY.
    """
    from models.avatar import AvatarStatus, AvatarPhase
    from services.r2_storage import get_r2_storage_service

    r2 = get_r2_storage_service()

    if status == "COMPLETED":
        video_b64 = output.get("video", "") if isinstance(output, dict) else ""
        video_url = ""
        if isinstance(output, dict) and not video_b64:
            video_url = (
                output.get("video_url")
                or output.get("result", {}).get("video_url")
                if isinstance(output.get("result"), dict) else ""
            ) or output.get("result") or ""
            if isinstance(video_url, dict):
                video_url = video_url.get("video_url", "")

        test_video_key = f"creators/{avatar.user_id}/avatar/{avatar.id}/test_video.mp4"
        video_uploaded = False

        if video_b64:
            video_bytes = base64.b64decode(video_b64)
            await r2.upload_bytes(video_bytes, test_video_key, content_type="video/mp4")
            video_uploaded = True
            logger.info(f"Avatar {avatar.id} test video uploaded (base64->R2): {len(video_bytes)} bytes")

        elif video_url and isinstance(video_url, str) and video_url.startswith("http"):
            import httpx
            try:
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    video_resp = await client.get(video_url)
                    video_resp.raise_for_status()
                    await r2.upload_bytes(video_resp.content, test_video_key, "video/mp4")
                    video_uploaded = True
                    logger.info(f"Avatar {avatar.id} test video uploaded (URL->R2): {len(video_resp.content)} bytes")
            except Exception as dl_err:
                sentry_sdk.capture_exception(dl_err)
                logger.error(f"Avatar {avatar.id} video download failed: {dl_err}")

        if video_uploaded:
            avatar.test_video_key = test_video_key
            avatar.status = AvatarStatus.READY
            avatar.active_phase = AvatarPhase.READY
            avatar.progress_step = "Avatar ready \u2014 review your test video"
            avatar.progress_percent = 100
            avatar.runpod_job_id = None  # Clear job_id after processing
            await db.commit()
            logger.info(f"Avatar {avatar.id} READY via webhook ({exec_time_ms / 1000:.0f}s render time)")
        else:
            avatar.status = AvatarStatus.FAILED
            avatar.active_phase = AvatarPhase.FAILED
            avatar.progress_step = "Failed: Video render completed but no video data returned"
            avatar.runpod_job_id = None
            await db.commit()
            logger.error(f"Avatar {avatar.id} completed but no video in output")

    elif status in ("FAILED", "TIMED_OUT"):
        error_msg = error or (output.get("error") if isinstance(output, dict) else "") or f"RunPod job {status}"
        avatar.status = AvatarStatus.FAILED
        avatar.active_phase = AvatarPhase.FAILED
        avatar.progress_step = f"Failed: {_normalize_error_message(str(error_msg)[:200])}"
        avatar.runpod_job_id = None
        await db.commit()
        logger.error(f"Avatar {avatar.id} test video failed: {error_msg}")

    else:
        logger.warning(f"Unexpected webhook status for avatar {avatar.id}: {status}")
        return {"status": "ignored", "reason": f"unexpected status: {status}"}

    return {"status": "processed", "avatar_id": avatar.id, "result": status}


@router.post("/wavespeed")
async def wavespeed_webhook(request: Request):
    """Receive InfiniteTalk job completion from WaveSpeed.

    WaveSpeed POSTs roughly the same shape as its poll-result endpoint:
    {"data" or top-level: {"id": "...", "status": "succeeded"|"completed"|
    "failed"|..., "outputs": ["<video_url>", ...], "error"/"message": "..."}}

    Only avatar preview jobs go through this path today (see
    tasks/generate_avatar.py._regenerate_pipeline) — cast-render blocks
    still use the blocking dispatcher cascade, not this webhook. Matched
    to an avatar via the ``runpod_job_id`` column using a ``wavespeed:``
    prefix (see WavespeedInfinitetalkProvider.submit_webhook), reusing the
    same column as the RunPod webhook path to avoid a DB migration.
    """
    payload = await request.json()
    data = payload.get("data") or payload
    prediction_id = data.get("id") or payload.get("id", "")
    status = data.get("status", "")
    outputs = data.get("outputs") or []
    error = data.get("error") or data.get("message") or ""

    logger.info(json.dumps({
        "service": "webhook",
        "event": "wavespeed_infinitetalk_result",
        "prediction_id": prediction_id,
        "status": status,
        "has_outputs": bool(outputs),
        "error": _normalize_error_message(str(error)[:200]) if error else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    if not prediction_id:
        raise HTTPException(400, "Missing prediction ID")

    job_id = f"wavespeed:{prediction_id}"

    from models.avatar import Avatar

    async with async_session_factory() as db:
        avatar = await db.scalar(
            select(Avatar).where(Avatar.runpod_job_id == job_id)
        )
        if not avatar:
            logger.warning(f"WaveSpeed webhook received for unknown job_id: {job_id}")
            return {"status": "ignored", "reason": "unknown job_id"}

        return await finalize_wavespeed_avatar_job(db, avatar, status, outputs, error, source="webhook")


async def finalize_wavespeed_avatar_job(db, avatar, status: str, outputs: list, error: str, source: str = "webhook") -> dict:
    """Shared finalize step for a WaveSpeed InfiniteTalk avatar-preview job —
    called from the ``/wavespeed`` webhook above AND from
    tasks.generate_avatar.reconcile_stale_avatar_wavespeed_jobs, the
    reconciliation sweep that catches jobs whose webhook never arrived
    (server mid-deploy, transient network blip, etc — the same class of gap
    tasks.generate_cast.cleanup_stale_rendering_jobs already covers for the
    older RunPod Variant pipeline). Downloads the rendered video, uploads to
    R2, and marks the avatar READY or FAILED. Does not commit/close ``db`` —
    callers own the session.
    """
    from models.avatar import AvatarStatus, AvatarPhase
    from services.r2_storage import get_r2_storage_service

    r2 = get_r2_storage_service()

    if status in ("succeeded", "completed") and outputs:
        video_url = outputs[0] if isinstance(outputs, list) else outputs
        test_video_key = f"creators/{avatar.user_id}/avatar/{avatar.id}/test_video.mp4"
        video_uploaded = False

        if isinstance(video_url, str) and video_url.startswith("http"):
            import httpx
            try:
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    video_resp = await client.get(video_url)
                    video_resp.raise_for_status()
                    await r2.upload_bytes(video_resp.content, test_video_key, "video/mp4")
                    video_uploaded = True
                    logger.info(f"Avatar {avatar.id} test video uploaded (WaveSpeed URL->R2, via {source}): {len(video_resp.content)} bytes")
            except Exception as dl_err:
                sentry_sdk.capture_exception(dl_err)
                logger.error(f"Avatar {avatar.id} WaveSpeed video download failed (via {source}): {dl_err}")

        if video_uploaded:
            avatar.test_video_key = test_video_key
            avatar.status = AvatarStatus.READY
            avatar.active_phase = AvatarPhase.READY
            avatar.progress_step = "Avatar ready — review your test video"
            avatar.progress_percent = 100
            avatar.runpod_job_id = None
            await db.commit()
            logger.info(f"Avatar {avatar.id} READY via WaveSpeed ({source})")
        else:
            avatar.status = AvatarStatus.FAILED
            avatar.active_phase = AvatarPhase.FAILED
            avatar.progress_step = "Failed: Video render completed but no video data returned"
            avatar.runpod_job_id = None
            await db.commit()
            logger.error(f"Avatar {avatar.id} WaveSpeed completed but video download failed (via {source})")

    elif status in ("failed", "canceled", "cancelled", "error"):
        error_msg = error or f"WaveSpeed job {status}"
        avatar.status = AvatarStatus.FAILED
        avatar.active_phase = AvatarPhase.FAILED
        avatar.progress_step = f"Failed: {_normalize_error_message(str(error_msg)[:200])}"
        avatar.runpod_job_id = None
        await db.commit()
        logger.error(f"Avatar {avatar.id} WaveSpeed test video failed (via {source}): {error_msg}")

    else:
        logger.warning(f"Unexpected WaveSpeed status for avatar {avatar.id} (via {source}): {status}")
        return {"status": "ignored", "reason": f"unexpected status: {status}"}

    return {"status": "processed", "avatar_id": avatar.id, "result": status}


def _populate_cast_final_video_url(cast, all_variants):
    """Populate cast.final_video_url from the first ready variant with a usable key.
    Safe to call multiple times — updates to composited key when it appears."""
    best_key = None
    composited_key = None
    for v in all_variants:
        status_str = (v.status.value if hasattr(v.status, 'value') else str(v.status)).upper()
        if status_str != "READY":
            continue
        if getattr(v, 'final_video_key', None) and not composited_key:
            composited_key = v.final_video_key
        if getattr(v, 'video_key', None) and not best_key:
            best_key = v.video_key
    chosen = composited_key or best_key
    if chosen:
        cast.final_video_url = f"https://media.luminacast.com/{chosen}"
        return True
    return False


async def _check_cast_completion(db, cast):
    """Check if all variants in a cast are done. Update cast status accordingly."""
    from models.variant import Variant, VariantStatus
    from models.block import Block
    from models.cast import CastStatus

    all_variants = (await db.execute(
        select(Variant).join(Block).where(Block.cast_id == cast.id)
    )).scalars().all()

    statuses = [v.status for v in all_variants]
    if not statuses:
        return

    # Count by status (handle both enum and string values)
    pending = sum(1 for s in statuses if (s.value if hasattr(s, "value") else str(s)).upper() in ("PENDING", "GENERATING"))
    ready = sum(1 for s in statuses if (s.value if hasattr(s, "value") else str(s)).upper() == "READY")
    failed = sum(1 for s in statuses if (s.value if hasattr(s, "value") else str(s)).upper() == "FAILED")
    total = len(statuses)

    if pending > 0:
        # Variants are still GENERATING/PENDING. The cast must NEVER move to
        # READY while any variant is in-flight — a premature READY surfaces a
        # half-rendered cast (and a fallback final_video_url) to the user. Only
        # update progress; the terminal status is decided below once every
        # variant has reached a terminal state (READY or FAILED).
        cast.generation_progress = 0.5 + (0.5 * (ready + failed) / total) if total else 0.5
        cast.progress_step = f"Rendering: {ready + failed}/{total} clips done ({ready} ready, {failed} failed)"
        await db.commit()
        return

    # All variants are done
    if ready == 0:
        cast.status = CastStatus.GENERATION_FAILED
        cast.generation_error = f"All {failed} clips failed to generate"
    elif failed > 0:
        cast.status = CastStatus.READY  # Partial success — usable but degraded
        cast.generation_error = f"{failed}/{total} clips failed. {ready} clips ready."
    else:
        cast.status = CastStatus.READY
        cast.generation_error = None

    cast.generation_progress = 1.0
    cast.progress_step = "Complete"

    # Populate cast-level final_video_url unconditionally on every READY path
    if ready > 0:
        _populate_cast_final_video_url(cast, all_variants)

    logger.info(f"Cast {cast.id} complete: {cast.status} ({ready} ready, {failed} failed)")

    await db.commit()

    # Run Twick timeline compositor if timeline_json is present
    if ready > 0 and cast.timeline_json:
        try:
            from tasks.generate_cast import _run_twick_compositor
            await _run_twick_compositor(cast.id)
            logger.info(f"Twick compositor ran for cast {cast.id}")
        except Exception as twick_err:
            sentry_sdk.capture_exception(twick_err)
            logger.warning(f"Twick compositor failed for cast {cast.id}: {twick_err}")

    # Broadcast completion via WebSocket
    try:
        from websocket.manager import ws_manager
        await ws_manager.broadcast_to_user(cast.user_id, {
            "type": "GENERATION_PROGRESS",
            "payload": {
                "cast_id": cast.id,
                "progress": 1.0,
                "current_step": "Complete" if ready > 0 else "Failed",
                "failed_count": failed,
            }
        })
    except Exception:
        pass


@router.post("/runpod/bs-roformer")
async def runpod_bs_roformer_webhook(request: Request):
    """Receive BS-RoFormer job completion from RunPod.
    Used for avatar voice pipeline — stores result in Redis for the waiting Celery task.
    """
    payload = await request.json()
    job_id = payload.get("id", "")
    status = payload.get("status", "")
    output = payload.get("output", {})
    error = payload.get("error", "")

    logger.info(json.dumps({
        "service": "webhook",
        "event": "runpod_bs_roformer_result",
        "job_id": job_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    # BS-RoFormer results feed back into the avatar generation task
    # Store the result in Redis for the waiting Celery task to pick up
    try:
        import redis.asyncio as aioredis
        from config import settings

        r = aioredis.from_url(settings.REDIS_URL)
        await r.set(
            f"runpod:result:{job_id}",
            json.dumps({"status": status, "output": output, "error": error}),
            ex=3600,  # Expire after 1 hour
        )
        await r.close()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Failed to store BS-RoFormer result in Redis: {e}")

    return {"status": "stored", "job_id": job_id}


# ── Stripe: subscriptions, PAYG credits ─────────────────────────────────


async def _sync_subscription(db, owner_id: str, sub_obj: dict, customer_id: str | None) -> None:
    """Upsert local `Subscription` state from a Stripe Subscription object
    (used by both `checkout.session.completed` and
    `customer.subscription.*`). Plan/interval are resolved from the
    subscription's actual Price ID first — falling back to the metadata
    set at checkout time only if the price isn't one we recognize — so a
    plan change made via the Stripe-hosted billing portal (which doesn't
    go through our own checkout metadata) still lands correctly.
    """
    from services import billing_service

    items = (sub_obj.get("items") or {}).get("data") or []
    first_item = items[0] if items else {}
    price_id = first_item.get("price", {}).get("id") if first_item.get("price") else None
    resolved = billing_service.resolve_plan_from_price_id(price_id) if price_id else None
    metadata = sub_obj.get("metadata") or {}
    plan = resolved[0] if resolved else metadata.get("plan", "starter")
    interval = resolved[1] if resolved else metadata.get("interval", "month")

    # api_version 2026-07-29.dahlia (services/stripe_billing.py::STRIPE_API_VERSION)
    # moved current_period_start/end off the top-level Subscription object
    # onto each subscription item (Stripe's multi-item flexible-billing
    # support) — the top-level fields are now always null. Read from the
    # first item, falling back to the top level in case a differently
    # api-versioned event ever reaches this handler.
    period_start = first_item.get("current_period_start") or sub_obj.get("current_period_start")
    period_end = first_item.get("current_period_end") or sub_obj.get("current_period_end")


    await billing_service.create_or_update_subscription_from_stripe(
        db,
        owner_id=owner_id,
        plan=plan,
        interval=interval,
        stripe_customer_id=customer_id or sub_obj.get("customer"),
        stripe_subscription_id=sub_obj.get("id"),
        stripe_price_id=price_id,
        current_period_start=billing_service.stripe_timestamp_to_naive_utc(period_start),
        current_period_end=billing_service.stripe_timestamp_to_naive_utc(period_end),
    )


async def _owner_id_for_stripe_subscription(db, stripe_subscription_id: str) -> str | None:
    from models.billing import Subscription

    return (
        await db.execute(
            select(Subscription.user_id).where(
                Subscription.stripe_subscription_id == stripe_subscription_id
            )
        )
    ).scalar_one_or_none()


async def _handle_stripe_event(db, event_type: str, obj: dict) -> None:
    from services import billing_service
    from services.billing_config import CREDIT_PACKS
    from services.stripe_billing import get_stripe_billing_service

    if event_type == "checkout.session.completed":
        mode = obj.get("mode")
        metadata = obj.get("metadata") or {}
        owner_id = metadata.get("owner_id")
        if not owner_id:
            logger.warning("checkout.session.completed with no owner_id metadata; ignoring")
            return

        if mode == "subscription":
            subscription_id = obj.get("subscription")
            if not subscription_id:
                return
            stripe_service = get_stripe_billing_service()
            sub_obj = await stripe_service.get_subscription(subscription_id)
            await _sync_subscription(db, owner_id, sub_obj, obj.get("customer"))

        elif mode == "payment":
            pack_id = metadata.get("pack_id")
            if not pack_id or pack_id not in CREDIT_PACKS:
                logger.warning("checkout.session.completed payment with unknown pack_id=%s", pack_id)
                return
            payment_intent_id = obj.get("payment_intent")
            await billing_service.purchase_credits(
                db,
                owner_id,
                pack_id,
                stripe_payment_intent_id=payment_intent_id,
                stripe_checkout_session_id=obj.get("id"),
            )
            # Save the card used so overage/auto-top-up can charge it
            # off-session later without asking the user to check out again.
            if payment_intent_id:
                try:
                    stripe_service = get_stripe_billing_service()
                    intent = await stripe_service.get_payment_intent(payment_intent_id)
                    pm_id = intent.get("payment_method")
                    if pm_id:
                        wallet = await billing_service.get_or_create_credit_wallet(db, owner_id)
                        wallet.stripe_payment_method_id = pm_id
                        await db.commit()
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)

    elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
        metadata = obj.get("metadata") or {}
        owner_id = metadata.get("owner_id")
        if not owner_id:
            # Subscriptions created directly in the Stripe dashboard (not
            # via our checkout) won't carry our metadata — nothing to
            # attribute this to locally, safe to ignore.
            logger.info("%s with no owner_id metadata; ignoring", event_type)
            return
        await _sync_subscription(db, owner_id, obj, obj.get("customer"))
        if obj.get("status") in ("past_due", "unpaid"):
            await billing_service.mark_subscription_past_due(db, owner_id)

    elif event_type == "customer.subscription.deleted":
        metadata = obj.get("metadata") or {}
        owner_id = metadata.get("owner_id")
        if owner_id:
            await billing_service.mark_subscription_terminated(db, owner_id)

    elif event_type == "invoice.payment_failed":
        sub_id = obj.get("subscription")
        if sub_id:
            owner_id = await _owner_id_for_stripe_subscription(db, sub_id)
            if owner_id:
                await billing_service.mark_subscription_past_due(db, owner_id)

    elif event_type == "invoice.paid":
        # The only place a real subscription payment amount ever gets
        # recorded — Subscription/SubscriptionEvent never stored one. See
        # SubscriptionPayment's docstring (models/billing.py).
        await billing_service.record_subscription_payment(db, obj)

    else:
        logger.info("Unhandled Stripe event type: %s", event_type)


@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook receiver — subscriptions + PAYG credit purchases.

    Idempotency: `ProcessedStripeEvent` has `id` (the Stripe event id) as
    its primary key. The insert is attempted BEFORE any billing side
    effect runs; a unique-constraint violation means this exact event was
    already delivered (duplicate delivery, or a retried delivery after our
    200 was lost in transit) and is a no-op 200 rather than re-applying
    the event. This is what makes "payment succeeds but webhook arrives
    late" and "duplicate webhook delivery" both safe.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    from services.stripe_billing import get_stripe_billing_service

    stripe_service = get_stripe_billing_service()
    try:
        event = await stripe_service.construct_webhook_event(payload, sig_header)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_id = event.get("id")
    event_type = event.get("type", "")
    data_object = (event.get("data") or {}).get("object", {})

    from models.billing import ProcessedStripeEvent

    async with async_session_factory() as db:
        db.add(ProcessedStripeEvent(id=event_id, event_type=event_type))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.info("Stripe webhook %s (%s) already processed — skipping", event_id, event_type)
            return {"status": "already_processed"}

        try:
            await _handle_stripe_event(db, event_type, data_object)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.error(
                "Stripe webhook handling failed for %s (%s): %s", event_id, event_type, exc
            )
            # Still 200 — the event is recorded as processed and the
            # failure is in Sentry for manual reconciliation. Asking
            # Stripe to retry a handler that just failed deterministically
            # (e.g. a bad price mapping) would only spin.

    return {"status": "processed"}