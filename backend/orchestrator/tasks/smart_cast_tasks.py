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
            blk.user_video_asset_id = asset_id
            imported += 1
            await db.commit()

    logger.info("Smart Cast stock import for %s: %d imported, %d failed", cast_id, imported, failed)
    return {"cast_id": cast_id, "imported": imported, "failed": failed}
