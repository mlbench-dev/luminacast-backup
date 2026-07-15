"""TikTok Shop product scraping via Apify."""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

import httpx
from config import settings

logger = logging.getLogger(__name__)

APIFY_ACTOR = "pro100chok~tiktok-shop-scraper"
APIFY_BASE = "https://api.apify.com/v2"

# Mock data for development when APIFY_API_TOKEN is not set
MOCK_PRODUCTS = [
    {
        "product_id": "mock_001",
        "title": "Rare Beauty Soft Pinch Liquid Blush",
        "product_url": "https://shop.tiktok.com/mock/001",
        "current_price": "$23.00",
        "original_price": "$26.00",
        "discount_percent": 12,
        "sales_volume": 284000,
        "rating": 4.8,
        "review_count": 12400,
        "seller_name": "Rare Beauty Official",
        "image_urls": [],
        "tags": ["Best Seller", "Free Shipping"],
        "search_rank": 1,
        "category": "Beauty",
    },
    {
        "product_id": "mock_002",
        "title": "CeraVe Moisturizing Cream",
        "product_url": "https://shop.tiktok.com/mock/002",
        "current_price": "$18.99",
        "original_price": "$19.99",
        "discount_percent": 5,
        "sales_volume": 156000,
        "rating": 4.7,
        "review_count": 8200,
        "seller_name": "CeraVe Store",
        "image_urls": [],
        "tags": ["Free Shipping"],
        "search_rank": 2,
        "category": "Beauty",
    },
    {
        "product_id": "mock_003",
        "title": "Dyson Airwrap Multi-Styler",
        "product_url": "https://shop.tiktok.com/mock/003",
        "current_price": "$499.99",
        "original_price": "$599.99",
        "discount_percent": 17,
        "sales_volume": 42000,
        "rating": 4.6,
        "review_count": 3100,
        "seller_name": "Dyson Official",
        "image_urls": [],
        "tags": ["Best Seller", "Free Shipping"],
        "search_rank": 3,
        "category": "Beauty",
    },
    {
        "product_id": "mock_004",
        "title": "Summer Fridays Lip Butter Balm",
        "product_url": "https://shop.tiktok.com/mock/004",
        "current_price": "$24.00",
        "original_price": "$24.00",
        "discount_percent": 0,
        "sales_volume": 198000,
        "rating": 4.5,
        "review_count": 9800,
        "seller_name": "Summer Fridays",
        "image_urls": [],
        "tags": ["Trending"],
        "search_rank": 4,
        "category": "Beauty",
    },
]


class TikTokShopService:

    async def search_products(self, query: str, max_products: int = 50) -> list[dict]:
        """Search TikTok Shop. Returns normalized product list.

        If APIFY_API_TOKEN is not set, returns mock data for development.
        """
        token = settings.APIFY_API_TOKEN
        if not token:
            logger.info("APIFY_API_TOKEN not set — returning mock TikTok Shop data")
            return [p for p in MOCK_PRODUCTS if query.lower() in p["title"].lower() or query.lower() in p["category"].lower()] or MOCK_PRODUCTS[:max_products]

        async with httpx.AsyncClient(timeout=120) as client:
            # Start actor run
            resp = await client.post(
                f"{APIFY_BASE}/acts/{APIFY_ACTOR}/runs",
                params={"token": token},
                json={
                    "queries": [query],
                    "maxItems": max_products,
                },
            )
            if resp.status_code == 403:
                error_data = resp.json().get("error", {})
                error_type = error_data.get("type", "")
                if "not-rented" in error_type or "trial" in error_type:
                    raise RuntimeError("TikTok Shop scraper subscription expired — please rent the actor at https://console.apify.com/actors/8WYlVqei2xfY3RaLX")
                raise RuntimeError(f"Apify access denied: {error_data.get('message', resp.text[:200])}")
            resp.raise_for_status()
            run_data = resp.json()["data"]
            run_id = run_data["id"]
            dataset_id = run_data["defaultDatasetId"]

            # Poll for completion (max 2 minutes)
            for _ in range(24):
                await asyncio.sleep(5)
                status_resp = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    params={"token": token},
                )
                status = status_resp.json()["data"]["status"]
                if status == "SUCCEEDED":
                    break
                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise RuntimeError(f"Apify run {status}")

            # Fetch results
            items_resp = await client.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items",
                params={"token": token, "format": "json"},
            )
            items = items_resp.json()

        # Normalize
        products = []
        for item in items:
            products.append(self._normalize_item(item))

        return products


    @staticmethod
    def _format_price(value) -> str:
        """Ensure price is a string like '$X.XX'."""
        if value is None or value == "":
            return "$0.00"
        if isinstance(value, (int, float)):
            return f"${value:.2f}"
        s = str(value).strip()
        if not s:
            return "$0.00"
        # Already formatted with $
        if s.startswith("$"):
            return s
        # Numeric string without $
        try:
            return f"${float(s):.2f}"
        except ValueError:
            return s

    @staticmethod
    def _parse_number(value, default=0) -> float:
        """Parse a numeric value that might be a string with symbols like '%', ',', '$'."""
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
        try:
            return float(s) if s else float(default)
        except ValueError:
            return float(default)

    def _normalize_item(self, item: dict) -> dict:
        """Normalize a single Apify result item to our standard schema."""
        return {
            "product_id": str(item.get("productId") or item.get("id") or ""),
            "title": str(item.get("title") or item.get("name") or ""),
            "product_url": str(item.get("productUrl") or item.get("url") or ""),
            "current_price": self._format_price(item.get("currentPrice") or item.get("price")),
            "original_price": self._format_price(item.get("originalPrice") or item.get("original_price")),
            "discount_percent": self._parse_number(item.get("discountPercent") or item.get("discount")),
            "sales_volume": int(self._parse_number(item.get("salesVolume") or item.get("sold"))),
            "rating": self._parse_number(item.get("rating") or item.get("stars")),
            "review_count": int(self._parse_number(item.get("reviewCount") or item.get("reviews"))),
            "seller_name": str(item.get("sellerName") or item.get("shopName") or ""),
            "image_urls": item.get("imageUrls") or item.get("images") or [],
            "tags": item.get("tags") or [],
            "search_rank": int(self._parse_number(item.get("searchRank"))),
            "category": str(item.get("category") or ""),
        }


_instance: TikTokShopService | None = None


def get_tiktok_shop_service() -> TikTokShopService:
    global _instance
    if _instance is None:
        _instance = TikTokShopService()
    return _instance
