"""Product discovery background tasks."""

import asyncio
import logging
import sentry_sdk
from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="refresh_trending_products")
def refresh_trending_products():
    """Refresh trending product cache from Apify. Runs daily via celery-beat."""
    from services.product_discovery import ProductDiscoveryService

    async def _refresh():
        service = ProductDiscoveryService()
        for section in ["top_selling", "trending", "flash_sale", "new", "high_potential"]:
            try:
                await service.fetch_trending(
                    section=section, region="US", max_items=100, force_refresh=True,
                )
                logger.info("Refreshed trending: %s", section)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error("Failed to refresh %s: %s", section, e)

    asyncio.run(_refresh())
