"""Celery task for channel content indexing."""

import asyncio
import logging
from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1)
def index_channel_content(self, channel_id: str, user_id: str):
    """Index a channel's content — scrape videos, filter, build voice profile."""
    logger.info(f"Starting channel indexing: {channel_id}")
    try:
        from services.content_indexer import ContentIndexer
        indexer = ContentIndexer()
        asyncio.run(indexer.index_channel(channel_id))
        logger.info(f"Channel indexing complete: {channel_id}")
    except Exception as e:
        logger.error(f"Channel indexing failed: {channel_id} — {e}")
        # Mark channel as failed
        try:
            from database import async_session_factory
            from models.channel import Channel

            async def _mark_failed():
                async with async_session_factory() as db:
                    channel = await db.get(Channel, channel_id)
                    if channel:
                        channel.index_status = "failed"
                        await db.commit()

            asyncio.run(_mark_failed())
        except Exception:
            pass
        raise
