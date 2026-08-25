"""AI-generated product b-roll for casts with broll_media_source='ai_generated'.

Runs after /casts/{id}/generate-smart-outline returns. Pexels auto-population
already ran in-band (cheap, ~150-400ms/block) and left every stock_photo/
stock_video block's stock_media_url populated as a safety-net fallback. This
task then replaces that fallback with a product-only AI-generated photo/video
(FLUX Kontext / Kling, services/product_ai_media.py) for any such block that
has a product attached and no per-block visual override — but only when the
cast opted into "ai_generated" b-roll at Setup.

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


async def _generate_async(cast_id: str) -> dict:
    from database import async_session_factory
    from models.block import Block
    from models.cast import Cast
    from models.product import Product
    from services.product_ai_media import (
        generate_ai_image_asset,
        generate_ai_video_asset,
        ProductAiMediaError,
    )

    generated = 0
    failed = 0

    async with async_session_factory() as db:
        cast = await db.get(Cast, cast_id)
        if cast is None:
            return {"error": "cast_not_found", "generated": 0}
        if getattr(cast, "broll_media_source", "stock") != "ai_generated":
            return {"skipped": "not_ai_generated", "generated": 0}
        owner_id = cast.user_id

        rows = (
            await db.execute(
                select(Block).where(
                    Block.cast_id == cast_id,
                    Block.deleted_at.is_(None),
                    Block.category.in_(("stock_photo", "stock_video")),
                    Block.product_id.isnot(None),
                    Block.video_asset_id.is_(None),
                    Block.image_asset_id.is_(None),
                )
            )
        ).scalars().all()

        for blk in rows:
            product = await db.get(Product, blk.product_id)
            if product is None:
                continue
            try:
                if blk.category == "stock_photo":
                    asset = await generate_ai_image_asset(
                        product, db, owner_id, style="lifestyle",
                    )
                    await db.flush()
                    blk.image_asset_id = asset.id
                else:
                    asset = await generate_ai_video_asset(
                        product, db, owner_id, style="product_showcase",
                        duration_seconds=5, quality="pro",
                    )
                    await db.flush()
                    blk.video_asset_id = asset.id
                await db.commit()
                generated += 1
            except ProductAiMediaError as exc:
                logger.warning(
                    "AI b-roll generation failed for block %s (%s); "
                    "leaving Pexels stock_media_url as the fallback",
                    blk.id, exc,
                )
                failed += 1
            except Exception as exc:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
                logger.warning("AI b-roll generation errored for block %s: %s", blk.id, exc)
                failed += 1

    logger.info("AI b-roll for cast %s: %d generated, %d failed", cast_id, generated, failed)
    return {"cast_id": cast_id, "generated": generated, "failed": failed}
