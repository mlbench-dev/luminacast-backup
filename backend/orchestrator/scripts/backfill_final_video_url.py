"""Backfill final_video_url for READY casts that have it null.

Walks all READY casts with null final_video_url, finds their first ready variant
with a video key (preferring composited final_video_key over raw video_key),
and populates the cast's final_video_url.

Run once against production:
    docker compose exec orchestrator python -m scripts.backfill_final_video_url
"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def backfill():
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    from models.cast import Cast, CastStatus
    from models.variant import Variant
    from models.block import Block

    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    fixed = 0
    skipped = 0

    async with factory() as session:
        # Find all READY casts with null final_video_url
        casts = (await session.execute(
            select(Cast).where(
                Cast.status == CastStatus.READY,
                Cast.final_video_url.is_(None),
            )
        )).scalars().all()

        logger.info("Found %d READY casts with null final_video_url", len(casts))

        for cast in casts:
            all_variants = (await session.execute(
                select(Variant).join(Block).where(Block.cast_id == cast.id)
            )).scalars().all()

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
                fixed += 1
                logger.info("Fixed cast %s: final_video_url = %s", cast.id, cast.final_video_url)
            else:
                skipped += 1
                logger.warning("Cast %s: READY but no variant has a video key", cast.id)

        await session.commit()

    await engine.dispose()
    logger.info("Backfill complete: %d fixed, %d skipped (no video key)", fixed, skipped)
    return fixed, skipped


if __name__ == "__main__":
    fixed, skipped = asyncio.run(backfill())
    sys.exit(0 if skipped == 0 else 1)
