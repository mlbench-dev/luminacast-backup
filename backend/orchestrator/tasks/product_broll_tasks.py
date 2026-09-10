"""AI-generated product b-roll for casts with broll_media_source='ai_generated'.

Runs after outline generation. Pexels auto-population already ran in-band and
left each b-roll beat with a stock clip (on stock_photo/stock_video blocks via
stock_media_url, and on avatar_speaking / avatar_voiceover beats via
parallel_media). This task replaces those generic stock picks with a
product-only AI-generated photo/video (FLUX Kontext / Kling,
services/product_ai_media.py) so the b-roll actually shows the user's product.

It processes ANY block that carries b-roll (a stock_* category, a
parallel_media list, or a stock_media_url) and resolves a product for it —
its own product_id, else the cast's primary product. Earlier this only looked
at stock_photo/stock_video *category* blocks, which the Auto Cast (Smart Cast)
outline almost never produces — it builds avatar_speaking / avatar_voiceover
beats with parallel_media instead — so "AI-generated from product" silently
did nothing on those casts.

Generated assets are cached per (product, kind, prompt) so a single-product
cast makes one image / one video and reuses it across beats rather than firing
a Kling call per block.

Why a Celery task, not inline in the outline endpoint: a single Kling video
generation takes on the order of minutes (see services/product_ai_media.py),
so generating even one in-band would blow past Cloudflare's edge timeout —
the same reasoning as tasks/smart_cast_tasks.py's Pexels-to-R2 import. The
renderer already prefers Block.video_asset_id/image_asset_id over
stock_media_url (tasks/cast_render.py resolve_voiceover_visual_sources), so a
render that starts before this task finishes still gets the Pexels fallback,
and a render that starts after gets the AI-generated asset — no coordination
needed between the two.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.product_broll_tasks.generate_ai_broll_for_cast", queue="default")
def generate_ai_broll_for_cast_task(cast_id: str) -> dict:
    return asyncio.run(_generate_async(cast_id))


def _block_has_broll(blk) -> bool:
    """Does this block carry an auto-picked stock visual we should replace?"""
    if (blk.category or "") in ("stock_photo", "stock_video"):
        return True
    pm = blk.parallel_media
    if isinstance(pm, list) and any(isinstance(x, dict) and x.get("url") for x in pm):
        return True
    return bool(blk.stock_media_url)


async def _generate_async(cast_id: str) -> dict:
    from database import async_session_factory
    from models.block import Block
    from models.cast import Cast, CastProduct
    from models.product import Product
    from services.product_ai_media import (
        generate_ai_image_asset,
        generate_ai_video_asset,
        ProductAiMediaError,
    )

    generated = 0
    failed = 0
    reused = 0

    async with async_session_factory() as db:
        cast = await db.get(Cast, cast_id)
        if cast is None:
            return {"error": "cast_not_found", "generated": 0}
        from services.ai_broll_models import parse_broll_source, endpoint_for, cost_for
        _is_ai, _broll_model_id = parse_broll_source(getattr(cast, "broll_media_source", "stock"))
        if not _is_ai:
            return {"skipped": "not_ai_generated", "generated": 0}
        _t2v_endpoint = endpoint_for(_broll_model_id, "t2v")
        _i2v_endpoint = endpoint_for(_broll_model_id, "i2v")
        logger.info("AI b-roll for cast %s: model=%s", cast_id, _broll_model_id)
        owner_id = cast.user_id

        # Cast primary product — the fallback when a b-roll block has no
        # product_id of its own (Smart Cast leaves many beats' product_id NULL).
        primary_product_id = None
        cp = (
            await db.execute(
                select(CastProduct)
                .where(CastProduct.cast_id == cast_id)
                .order_by(CastProduct.position.asc())
                .limit(1)
            )
        ).scalars().first()
        if cp:
            primary_product_id = cp.product_id

        rows = (
            await db.execute(
                select(Block).where(
                    Block.cast_id == cast_id,
                    Block.deleted_at.is_(None),
                    Block.video_asset_id.is_(None),
                    Block.image_asset_id.is_(None),
                )
            )
        ).scalars().all()

        # Pre-pass: the blocks we'll actually work on. Stamp each with
        # metadata.ai_broll="generating" so the Script tab can show a
        # "creating your product shot" state (and poll for completion)
        # instead of presenting the interim Pexels clip as the final pick.
        from services.ai_broll import ai_broll_enabled
        _scene_broll_ok = ai_broll_enabled()
        eligible: list = []
        for blk in rows:
            if not _block_has_broll(blk):
                continue
            has_product = bool(blk.product_id or primary_product_id)
            has_query = bool((blk.stock_media_query or "").strip())
            # Product beats always eligible (product shot). Product-less beats
            # only when AI_BROLL_ENABLED and they have a query to generate from
            # — otherwise there's nothing to anchor the shot on, keep Pexels.
            if not has_product and not (_scene_broll_ok and has_query):
                continue
            blk.block_metadata = {**(blk.block_metadata or {}), "ai_broll": "generating"}
            eligible.append(blk)
        if not eligible:
            logger.info("AI b-roll for cast %s: no eligible b-roll blocks", cast_id)
            return {"cast_id": cast_id, "generated": 0, "reused": 0, "failed": 0}
        await db.commit()

        # (product_id, kind, prompt) -> ProductAsset, so a single-product cast
        # makes one asset and reuses it instead of a Kling call per block.
        asset_cache: dict[tuple, object] = {}

        for blk in eligible:
            product_id = blk.product_id or primary_product_id
            product = await db.get(Product, product_id) if product_id else None

            # No product to anchor the shot on → generate the described scene
            # directly via Kling text-to-video (flag-gated). Falls back to the
            # stock clip on any failure.
            if product is None:
                try:
                    from services.ai_broll import (
                        generate_scene_broll_video, broll_aspect_ratio,
                    )
                    url, cost = await generate_scene_broll_video(
                        prompt_query=(blk.stock_media_query or "").strip(),
                        owner_id=owner_id,
                        aspect_ratio=broll_aspect_ratio(blk),
                        duration_seconds=5,
                        model_endpoint=_t2v_endpoint,
                    )
                    blk.image_asset_id = None
                    blk.video_asset_id = None
                    blk.parallel_media = [{
                        "kind": "video", "url": url, "thumbnail": url,
                        "source": "ai_generated", "start_offset_s": 0,
                        "duration_s": None,
                    }]
                    blk.block_metadata = {
                        **(blk.block_metadata or {}), "ai_broll": "done",
                    }
                    await db.commit()
                    generated += 1
                    try:
                        from services.usage_tracker import log_usage
                        await log_usage(
                            db, user_id=owner_id, event_type="ai_broll_scene",
                            provider="fal_ai",
                            provider_cost_usd=cost_for(_broll_model_id), quantity=1,
                            quantity_unit="videos", resource_type="cast",
                            resource_id=cast_id, provider_model=_t2v_endpoint,
                        )
                        await db.commit()
                    except Exception as _uexc:
                        import sentry_sdk
                        sentry_sdk.capture_exception(_uexc)
                        await db.rollback()
                except Exception as exc:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                    logger.warning(
                        "scene b-roll generation failed for block %s (%s); "
                        "leaving the stock clip as the fallback",
                        blk.id, exc,
                    )
                    await db.rollback()
                    blk2 = await db.get(Block, blk.id)
                    if blk2 is not None:
                        blk2.block_metadata = {
                            **(blk2.block_metadata or {}), "ai_broll": "failed",
                        }
                        await db.commit()
                    failed += 1
                continue

            # Scene prompt from the beat's own stock query, so each shot is the
            # real product framed for that beat ("hoodie flatlay", "morning
            # coffee window", ...). Falls back to the generic lifestyle style.
            prompt = (blk.stock_media_query or "").strip()
            # stock_video / avatar_voiceover beats are the visual — give them
            # motion (Kling). Brief cutaways over a speaking avatar get a still
            # (FLUX Kontext — far faster); the renderer Ken-Burns-animates it.
            want_video = (blk.category or "") == "stock_video" or (
                (blk.category or "") == "avatar_voiceover"
            )
            kind = "video" if want_video else "image"
            cache_key = (product_id, kind, prompt)

            try:
                asset = asset_cache.get(cache_key)
                if asset is not None:
                    reused += 1
                else:
                    if kind == "video":
                        asset = await generate_ai_video_asset(
                            product, db, owner_id, style="product_showcase",
                            duration_seconds=5, quality="pro",
                            custom_prompt=prompt,
                            model_endpoint=_i2v_endpoint,
                        )
                    else:
                        asset = await generate_ai_image_asset(
                            product, db, owner_id, style="lifestyle",
                            custom_prompt=prompt,
                        )
                    await db.flush()
                    asset_cache[cache_key] = asset
                    generated += 1

                media_type = getattr(asset, "media_type", "image")
                if media_type == "video":
                    blk.image_asset_id = None
                    blk.video_asset_id = asset.id
                else:
                    blk.video_asset_id = None
                    blk.image_asset_id = asset.id
                # Point parallel_media at the AI asset too, so the editor's
                # "Visual b-roll" strip shows the product shot, not the Pexels
                # clip it replaced.
                blk.parallel_media = [{
                    "kind": "video" if media_type == "video" else "photo",
                    "url": asset.r2_url,
                    "thumbnail": asset.r2_url,
                    "source": "ai_generated",
                    "start_offset_s": 0,
                    "duration_s": None,
                }]
                blk.block_metadata = {**(blk.block_metadata or {}), "ai_broll": "done"}
                await db.commit()
            except (ProductAiMediaError, Exception) as exc:
                if not isinstance(exc, ProductAiMediaError):
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                logger.warning(
                    "AI b-roll generation failed for block %s (%s); "
                    "leaving the stock clip as the fallback",
                    blk.id, exc,
                )
                await db.rollback()
                # Re-stamp after rollback (rollback discarded the in-session
                # metadata change) so the UI stops showing the spinner.
                blk2 = await db.get(Block, blk.id)
                if blk2 is not None:
                    blk2.block_metadata = {**(blk2.block_metadata or {}), "ai_broll": "failed"}
                    await db.commit()
                failed += 1

    logger.info(
        "AI b-roll for cast %s: %d generated, %d reused, %d failed",
        cast_id, generated, reused, failed,
    )
    return {"cast_id": cast_id, "generated": generated, "reused": reused, "failed": failed}
