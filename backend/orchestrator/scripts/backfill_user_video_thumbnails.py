"""Backfill thumbnail_r2_key for existing user_video_assets rows.

Walks every non-deleted UserVideoAsset whose `thumbnail_r2_key` is null,
downloads the source MP4 from R2, extracts a JPEG thumbnail with ffmpeg,
uploads it back to R2, and updates the row. Per-row failures are logged
and reported to Sentry but do not abort the loop, so the script is safe
to re-run — rows that now have a thumbnail are skipped.

Run once against production:
    docker compose exec orchestrator python -m scripts.backfill_user_video_thumbnails
"""
import asyncio
import logging
import sys

import sentry_sdk

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def backfill() -> tuple[int, int, int]:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from config import settings
    from models.user_video import UserVideoAsset
    from services.r2_storage import get_r2_storage_service
    from services.video_thumbnail import extract_video_thumbnail_jpeg

    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    r2 = get_r2_storage_service()
    fixed = 0
    skipped = 0
    failed = 0

    async with factory() as session:
        rows = (await session.execute(
            select(UserVideoAsset).where(
                UserVideoAsset.deleted_at.is_(None),
                UserVideoAsset.thumbnail_r2_key.is_(None),
            )
        )).scalars().all()

        logger.info("Found %d user video assets needing thumbnails", len(rows))

        for asset in rows:
            try:
                # Pull the source MP4 down so we can run ffmpeg against it.
                import tempfile
                import os
                src_tmp: str | None = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                        src_tmp = tmp.name
                    await r2.download_file(asset.r2_key, src_tmp)
                    with open(src_tmp, "rb") as fh:
                        video_bytes = fh.read()
                finally:
                    if src_tmp and os.path.exists(src_tmp):
                        try:
                            os.unlink(src_tmp)
                        except Exception as e:
                            sentry_sdk.capture_exception(e)

                if not video_bytes:
                    logger.warning("asset %s: empty source bytes, skipping", asset.id)
                    skipped += 1
                    continue

                thumb_bytes = await extract_video_thumbnail_jpeg(video_bytes)
                if not thumb_bytes:
                    logger.warning("asset %s: thumbnail extraction returned empty", asset.id)
                    failed += 1
                    continue

                thumb_key = f"user-videos/{asset.user_id}/{asset.id}_thumb.jpg"
                await r2.upload_bytes(thumb_bytes, thumb_key, content_type="image/jpeg")
                asset.thumbnail_r2_key = thumb_key
                await session.commit()
                fixed += 1
                logger.info("asset %s: thumbnail stored at %s", asset.id, thumb_key)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.exception("asset %s: backfill failed", asset.id)
                failed += 1
                try:
                    await session.rollback()
                except Exception as rb_err:
                    sentry_sdk.capture_exception(rb_err)

    await engine.dispose()
    logger.info(
        "Backfill complete: %d fixed, %d failed, %d skipped",
        fixed, failed, skipped,
    )
    return fixed, failed, skipped


if __name__ == "__main__":
    fixed, failed, skipped = asyncio.run(backfill())
    sys.exit(0 if failed == 0 else 1)
