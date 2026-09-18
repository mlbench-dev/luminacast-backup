"""Smart Cast follow-up tasks.

Runs after `/casts/{id}/generate-smart-outline` returns. The endpoint
attaches Pexels CDN URLs to each block (cheap, in-band). This task then
downloads each asset, uploads to R2, creates UserVideoAsset rows, and
points each block's `user_video_asset_id` at the imported asset so the
timeline can render from R2 (which we control + cache) instead of the
Pexels CDN.

Why a Celery task: a single video can be 30 MB; importing 6 of them
synchronously in the outline endpoint would blow past Cloudflare's
~100s edge timeout (we already lost a body-shots cycle to that). The
endpoint returns fast with Pexels URLs; this task upgrades to R2 in the
background. The editor reads `user_video_asset_id` first, falling back
to `stock_media_url` if the import is still pending.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from sqlalchemy import select

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.smart_cast_tasks.import_smart_stock", queue="default")
def import_smart_stock_task(cast_id: str) -> dict:
    return asyncio.run(_import_async(cast_id))


async def _import_async(cast_id: str) -> dict:
    from database import async_session_factory
    from models.block import Block
    from models.user_video import UserVideoAsset
    from models.cast import Cast
    from services.r2_storage import get_r2_storage_service

    r2 = get_r2_storage_service()
    imported = 0
    failed = 0

    async with async_session_factory() as db:
        cast = await db.get(Cast, cast_id)
        if cast is None:
            return {"error": "cast_not_found", "imported": 0}
        user_id = cast.user_id

        rows = (
            await db.execute(
                select(Block).where(
                    Block.cast_id == cast_id,
                    Block.deleted_at.is_(None),
                    Block.stock_media_url.isnot(None),
                    Block.user_video_asset_id.is_(None),
                )
            )
        ).scalars().all()

        for blk in rows:
            kind = (blk.stock_media_kind or "").lower()
            url = blk.stock_media_url
            if not url:
                continue
            ext = "mp4" if kind == "video" else "jpg"
            content_type = "video/mp4" if kind == "video" else "image/jpeg"

            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    content = resp.content
            except Exception as exc:
                logger.warning("Smart Cast stock import download failed for %s: %s", blk.id, exc)
                failed += 1
                continue

            asset_id = f"uv_{uuid.uuid4().hex[:16]}"
            r2_key = f"user-videos/{user_id}/stock-imports/{asset_id}.{ext}"
            try:
                await r2.upload_bytes(content, r2_key, content_type=content_type)
            except Exception as exc:
                logger.warning("Smart Cast stock import upload failed for %s: %s", blk.id, exc)
                failed += 1
                continue

            db.add(UserVideoAsset(
                id=asset_id,
                user_id=user_id,
                name=f"Pexels {kind or 'asset'} {blk.stock_media_pexels_id or blk.id}",
                r2_key=r2_key,
                file_size_bytes=len(content),
                original_filename=f"pexels_{blk.stock_media_pexels_id or blk.id}.{ext}",
            ))
            # Flush the new UserVideoAsset row before pointing the block's
            # FK at it. blk.user_video_asset_id = asset_id is a bare scalar
            # assignment (not an ORM relationship reference), so SQLAlchemy
            # has no object-graph link telling it the block's UPDATE depends
            # on this INSERT — without an explicit flush here it isn't
            # guaranteed to emit the INSERT first, and Postgres checks the
            # FK constraint immediately per statement (not deferred),
            # raising ForeignKeyViolationError on the UPDATE.
            await db.flush()
            blk.user_video_asset_id = asset_id
            imported += 1
            await db.commit()

    logger.info("Smart Cast stock import for %s: %d imported, %d failed", cast_id, imported, failed)
    return {"cast_id": cast_id, "imported": imported, "failed": failed}


@celery_app.task(name="tasks.smart_cast_tasks.repopulate_stock_media", queue="default")
def repopulate_stock_media_task(cast_id: str) -> dict:
    """Re-run Pexels b-roll selection for a cast in ITS aspect orientation.

    Used after a cross-format "Duplicate as" — the copy inherits the source's
    stock_media_url / parallel_media, which were fetched for the SOURCE
    orientation (e.g. portrait clips on a now-16:9 cast → they get
    cover-cropped hard at render). This re-searches each block's
    stock_media_query as landscape / portrait to match the new format and
    writes the fresh picks back onto the block rows.
    """
    return asyncio.run(_repopulate_stock_async(cast_id))


async def _repopulate_stock_async(cast_id: str) -> dict:
    from database import async_session_factory
    from models.block import Block
    from models.cast import Cast, CastProduct
    from models.product import Product
    from engine.cast_generator import auto_populate_stock_media

    async with async_session_factory() as db:
        cast = await db.get(Cast, cast_id)
        if cast is None:
            return {"error": "cast_not_found"}

        orientation = (
            "landscape"
            if getattr(cast, "format_family", "vertical") == "horizontal"
            else "portrait"
        )

        blk_rows = (
            await db.execute(
                select(Block)
                .where(
                    Block.cast_id == cast_id,
                    Block.deleted_at.is_(None),
                    Block.stock_media_query.isnot(None),
                )
                .order_by(Block.position.asc())
            )
        ).scalars().all()
        if not blk_rows:
            return {"skipped": "no_stock_blocks", "cast_id": cast_id}

        cp_rows = (
            await db.execute(
                select(CastProduct)
                .where(CastProduct.cast_id == cast_id)
                .order_by(CastProduct.position.asc())
            )
        ).scalars().all()
        products: list[dict] = []
        for cp in cp_rows:
            p = await db.get(Product, cp.product_id)
            if p:
                # Vision-generated Pexels queries + visual description from
                # the product's own cover photo — cached on Product. See
                # services/product_stock_queries.py.
                from services.product_stock_queries import (
                    get_or_generate_product_ai_stock_queries,
                    get_or_generate_product_visual_description,
                )
                from services.r2_storage import get_r2_storage_service
                _r2 = get_r2_storage_service()
                ai_qs = await get_or_generate_product_ai_stock_queries(p, db, _r2)
                ai_desc = await get_or_generate_product_visual_description(p, db, _r2)
                products.append({
                    "name": p.name, "description": p.description or "",
                    "ai_stock_queries": ai_qs,
                    "ai_visual_description": ai_desc,
                })

        # `auto_populate_stock_media` mutates a list of outline-shaped dicts in
        # place; keep a ref to each source Block so we can write the picks back.
        outline: list[dict] = []
        for b in blk_rows:
            outline.append({
                "stock_media_query": b.stock_media_query,
                "category": b.category,
                "background_type": b.background_type,
                "product_name": None,
                "key_points": b.key_points,
                "_blk": b,
            })

        try:
            await auto_populate_stock_media(
                outline, cast_id, products=products or None, orientation=orientation,
            )

            updated = 0
            for od in outline:
                b = od["_blk"]
                new_url = od.get("stock_media_url")
                if new_url:
                    b.stock_media_url = new_url
                    b.stock_media_thumbnail = od.get("stock_media_thumbnail")
                    # Pexels' API returns `id` as a JSON int; the column is
                    # VARCHAR(40) and asyncpg's executemany (triggered here
                    # when multiple blocks flush in one commit) rejects a raw
                    # int for a string-typed bind param — cast explicitly.
                    # The outline-INSERT path already does this
                    # (generation.py); it never hit the mismatch because a
                    # single-row INSERT doesn't take the executemany path a
                    # multi-row UPDATE does.
                    pid = od.get("stock_media_pexels_id")
                    b.stock_media_pexels_id = str(pid) if pid is not None else None
                    b.stock_media_kind = od.get("stock_media_kind")
                    updated += 1
                if od.get("parallel_media") is not None:
                    b.parallel_media = od.get("parallel_media")
                # A cross-format re-fetch replaces the source's imported copy —
                # clear the stale R2 import pointer so the renderer uses the
                # fresh Pexels URL (import_smart_stock_task can re-host it later).
                if new_url:
                    b.user_video_asset_id = None

            await db.commit()
            logger.info(
                "repopulate_stock_media for %s: %d blocks updated (orientation=%s)",
                cast_id, updated, orientation,
            )
            return {"cast_id": cast_id, "orientation": orientation, "blocks_updated": updated}
        except Exception as exc:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
            logger.warning("repopulate_stock_media failed for %s: %s", cast_id, exc)
            # The failed statement may have left the session's transaction
            # unusable — roll back so this task exits cleanly rather than
            # propagating an uncaught error past Celery's own handler (same
            # class of bug fixed in refine_stock_media_from_script_task
            # above, which is where this was actually confirmed happening).
            await db.rollback()
            return {"error": "populate_failed", "cast_id": cast_id}


@celery_app.task(name="tasks.smart_cast_tasks.refine_stock_media_from_script", queue="default")
def refine_stock_media_from_script_task(cast_id: str) -> dict:
    """Re-run Pexels b-roll selection using each block's REAL delivered
    script line, once it exists.

    Outline generation searches b-roll using a block's `key_points` — a
    provisional framing written before the actual dialogue exists. The
    final script (generate-scripts) can drift from that framing (confirmed:
    key_points implied "noisy library" for a beat whose shipped line was
    "I spent years chasing that perfect studio sound…", producing
    library/bookshelf b-roll for a line that isn't about libraries at all —
    cst_b1ea8f3c8e4c block blk_362a9a55b436). This re-searches every
    b-roll-bearing block with its active Variant.script_text as the beat
    text (see `_block_beat_text` in engine/cast_generator.py), overwriting
    the outline-time pick when the real line finds a better match.

    Purely additive: the outline-time Pexels pass is untouched, so a block
    always has SOME b-roll immediately regardless of whether/when this task
    runs or succeeds.
    """
    return asyncio.run(_refine_stock_from_script_async(cast_id))


async def _refine_stock_from_script_async(cast_id: str) -> dict:
    from sqlalchemy.orm import selectinload
    from database import async_session_factory
    from models.block import Block
    from models.cast import Cast, CastProduct
    from models.product import Product
    from engine.cast_generator import auto_populate_stock_media

    async with async_session_factory() as db:
        cast = await db.get(Cast, cast_id)
        if cast is None:
            return {"error": "cast_not_found"}

        orientation = (
            "landscape"
            if getattr(cast, "format_family", "vertical") == "horizontal"
            else "portrait"
        )

        blk_rows = (
            await db.execute(
                select(Block)
                .where(
                    Block.cast_id == cast_id,
                    Block.deleted_at.is_(None),
                    Block.stock_media_query.isnot(None),
                )
                .options(selectinload(Block.variants))
                .order_by(Block.position.asc())
            )
        ).scalars().all()
        if not blk_rows:
            return {"skipped": "no_stock_blocks", "cast_id": cast_id}

        # Pre-pass: stamp every block we're about to re-search with
        # metadata.broll_refining="generating" and commit immediately, so
        # the Script tab's poll can see "in progress" from the start
        # (mirrors tasks/product_broll_tasks.py's identical pre-pass for
        # metadata.ai_broll).
        eligible: list[tuple] = []  # (block, script_text)
        for b in blk_rows:
            active_variant = next((v for v in b.variants if v.is_active), None)
            script_text = (active_variant.script_text or "").strip() if active_variant else ""
            if not script_text:
                continue
            b.block_metadata = {**(b.block_metadata or {}), "broll_refining": "generating"}
            eligible.append((b, script_text))
        if not eligible:
            return {"skipped": "no_scripted_blocks", "cast_id": cast_id}
        await db.commit()

        cp_rows = (
            await db.execute(
                select(CastProduct)
                .where(CastProduct.cast_id == cast_id)
                .order_by(CastProduct.position.asc())
            )
        ).scalars().all()
        products: list[dict] = []
        for cp in cp_rows:
            p = await db.get(Product, cp.product_id)
            if p:
                from services.product_stock_queries import (
                    get_or_generate_product_ai_stock_queries,
                    get_or_generate_product_visual_description,
                )
                from services.r2_storage import get_r2_storage_service
                _r2 = get_r2_storage_service()
                ai_qs = await get_or_generate_product_ai_stock_queries(p, db, _r2)
                ai_desc = await get_or_generate_product_visual_description(p, db, _r2)
                products.append({
                    "name": p.name, "description": p.description or "",
                    "ai_stock_queries": ai_qs,
                    "ai_visual_description": ai_desc,
                })

        outline: list[dict] = []
        for b, script_text in eligible:
            outline.append({
                "stock_media_query": b.stock_media_query,
                "category": b.category,
                "background_type": b.background_type,
                "product_name": None,
                "key_points": b.key_points,
                "script_text": script_text,
                "_blk": b,
            })

        try:
            await auto_populate_stock_media(
                outline, cast_id, products=products or None, orientation=orientation,
            )

            updated = 0
            for od in outline:
                b = od["_blk"]
                new_url = od.get("stock_media_url")
                if new_url:
                    b.stock_media_url = new_url
                    b.stock_media_thumbnail = od.get("stock_media_thumbnail")
                    # Pexels' API returns `id` as a JSON int; the column is
                    # VARCHAR(40) and asyncpg's executemany (triggered here
                    # since multiple blocks flush in one commit) rejects a
                    # raw int for a string-typed bind param — cast
                    # explicitly. The outline-INSERT path already does this
                    # (generation.py); it never hit the mismatch because a
                    # single-row INSERT doesn't take the executemany path a
                    # multi-row UPDATE does.
                    pid = od.get("stock_media_pexels_id")
                    b.stock_media_pexels_id = str(pid) if pid is not None else None
                    b.stock_media_kind = od.get("stock_media_kind")
                    b.user_video_asset_id = None
                    updated += 1
                if od.get("parallel_media") is not None:
                    b.parallel_media = od.get("parallel_media")
                b.block_metadata = {**(b.block_metadata or {}), "broll_refining": "done"}

            await db.commit()
            logger.info(
                "refine_stock_media_from_script for %s: %d/%d blocks updated",
                cast_id, updated, len(eligible),
            )
            return {"cast_id": cast_id, "blocks_refined": updated, "blocks_checked": len(eligible)}
        except Exception as exc:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "refine_stock_media_from_script failed for %s: %s", cast_id, exc,
            )
            # The failed statement may have left the session's transaction
            # unusable (Postgres aborts the whole transaction on error) — roll
            # back before the recovery write, or THIS commit fails too and
            # blocks stay stuck at "generating" forever. Confirmed: exactly
            # what happened before this fix (cst_cf9e0cea76ea) — a write-back
            # DataError (the pexels_id int/str mismatch above) propagated
            # past the original narrower try/except entirely uncaught, past
            # Celery's own handler, leaving every block permanently stuck.
            await db.rollback()
            for b, _ in eligible:
                b.block_metadata = {**(b.block_metadata or {}), "broll_refining": "failed"}
            await db.commit()
            return {"error": "refine_failed", "cast_id": cast_id}
