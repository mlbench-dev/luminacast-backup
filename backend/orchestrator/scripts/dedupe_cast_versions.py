"""Dedupe duplicate (cast_id, version) rows in cast_versions.

Before PR #77 there was no UNIQUE(cast_id, version) constraint on
`cast_versions`, so some casts accumulated multiple rows for the same
logical version. That broke the fork endpoint (MultipleResultsFound).

For every (cast_id, version) group with more than one row, this script
keeps the row with the most recent `created_at` and deletes the rest.
Per-group failures are reported to Sentry but do not abort the loop, so
the script is safe to re-run — already-deduped groups are skipped.

Run once against production (after PR #77 merges, before applying the
UNIQUE constraint migration):
    docker compose exec orchestrator python -m scripts.dedupe_cast_versions
"""
import asyncio
import logging
import sys

import sentry_sdk

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def dedupe() -> tuple[int, int, int]:
    """Returns (groups_processed, rows_deleted, groups_failed)."""
    from sqlalchemy import func, select
    from sqlalchemy import delete as sa_delete
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from config import settings
    from models.cast import CastVersion

    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    groups_processed = 0
    rows_deleted = 0
    groups_failed = 0

    async with factory() as session:
        try:
            dup_groups = (await session.execute(
                select(
                    CastVersion.cast_id,
                    CastVersion.version,
                    func.count().label("n"),
                )
                .group_by(CastVersion.cast_id, CastVersion.version)
                .having(func.count() > 1)
            )).all()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Failed to query duplicate cast_versions groups")
            await engine.dispose()
            return 0, 0, 1

        logger.info("Found %d (cast_id, version) groups with duplicates", len(dup_groups))

        for cast_id, version, count in dup_groups:
            try:
                rows = (await session.execute(
                    select(CastVersion)
                    .where(
                        CastVersion.cast_id == cast_id,
                        CastVersion.version == version,
                    )
                    .order_by(CastVersion.created_at.desc())
                )).scalars().all()

                if len(rows) <= 1:
                    # Concurrent delete or already deduped.
                    groups_processed += 1
                    continue

                keep = rows[0]
                to_delete_ids = [r.id for r in rows[1:]]

                await session.execute(
                    sa_delete(CastVersion).where(CastVersion.id.in_(to_delete_ids))
                )
                await session.commit()

                rows_deleted += len(to_delete_ids)
                groups_processed += 1
                logger.info(
                    "cast=%s version=%s: kept %s, deleted %d duplicate(s)",
                    cast_id, version, keep.id, len(to_delete_ids),
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.exception(
                    "cast=%s version=%s: dedupe failed", cast_id, version,
                )
                groups_failed += 1
                try:
                    await session.rollback()
                except Exception as rb_err:
                    sentry_sdk.capture_exception(rb_err)

    await engine.dispose()
    logger.info(
        "Dedupe complete: %d groups processed, %d rows deleted, %d groups failed",
        groups_processed, rows_deleted, groups_failed,
    )
    return groups_processed, rows_deleted, groups_failed


if __name__ == "__main__":
    try:
        processed, deleted, failed = asyncio.run(dedupe())
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("dedupe_cast_versions aborted")
        sys.exit(2)
    sys.exit(0 if failed == 0 else 1)
