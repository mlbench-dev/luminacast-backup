"""Product discovery via Apify TikTok Shop scrapers.

Three data modes:
1. Trending — fetch top-selling/trending products (home view, refreshed every 24h)
2. Search — keyword search with filters
3. Category — browse by TikTok Shop category

Results are cached in `trending_products` table for 24 hours.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import httpx
from config import settings
from database import async_session_factory
from models.trending_product import TrendingProduct
from sqlalchemy import select, func, or_

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"


def _synthesize_revenue_trend(total_revenue_cents: int, growth_rate_pct: float, days: int = 30) -> list:
    """Generate a plausible 30-day revenue trend sparkline.

    Synthesized from end-value (total revenue / 30) and growth rate.
    Uses a slight random walk around a linear growth line.
    Purely for visual trend indication — not real analytics.
    """
    import random
    if total_revenue_cents <= 0:
        return []
    end_daily = total_revenue_cents / days
    growth = max(growth_rate_pct, 0)
    start_daily = end_daily / (1 + growth / 100) if growth > 0 else end_daily * 0.85
    trend = []
    for i in range(days):
        frac = i / max(days - 1, 1)
        val = start_daily + frac * (end_daily - start_daily)
        jitter = val * random.uniform(-0.12, 0.12)
        trend.append(max(0, int(val + jitter)))
    return trend




def _parse_compact_number(value) -> "int | None":
    """Parse pro100chok compact numbers like '5.2K', '1.2M', '342', 5200."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        if s.upper().endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.upper().endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.upper().endswith("B"):
            return int(float(s[:-1]) * 1_000_000_000)
        return int(float(s))
    except (ValueError, TypeError):
        return None



class ProductDiscoveryService:

    def __init__(self):
        self.api_token = settings.APIFY_API_TOKEN

    # Map frontend section keys to parseforge output section names
    SECTION_MAP = {
        "top_selling": "Top Selling",
        "trending": "Trending",
        "flash_sale": "Discounted",
        "new": "Trending",         # no dedicated "new" section — reuse Trending
        "high_potential": "Top Selling",  # no dedicated section — reuse Top Selling
    }

    async def fetch_trending(
        self,
        section: str = "top_selling",
        region: str = "US",
        max_items: int = 200,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch trending products via parseforge/tiktok-shop-scraper. 24h cache."""

        # Check cache first
        if not force_refresh:
            cached = await self._get_cached(section=section, region=region, limit=max_items)
            if cached:
                return cached

        if not self.api_token:
            logger.error("APIFY_API_TOKEN not set — cannot fetch trending products")
            return []

        try:
            raw_products = await self._run_parseforge_trending()
            if not raw_products:
                logger.warning("parseforge returned 0 products, falling back to stale cache")
                stale = await self._get_cached_stale(section=section, region=region, limit=max_items)
                if stale:
                    logger.info("Returning %d stale cached products for section=%s", len(stale), section)
                return stale

            # Filter by the section that matches this tab
            target_section = self.SECTION_MAP.get(section, "Top Selling")
            section_products = [
                p for p in raw_products if p.get("section") == target_section
            ]
            if not section_products:
                section_products = raw_products  # fallback: use all

            await self._cache_products(section_products, section=section, region=region)
            return await self._get_cached(section=section, region=region, limit=max_items)

        except Exception as e:
            logger.exception("parseforge actor failed for section=%s: %s", section, e)
            # Return stale cache rather than empty — better UX than "Loading..." forever
            stale = await self._get_cached_stale(section=section, region=region, limit=max_items)
            if stale:
                logger.info("Returning %d stale cached products after Apify failure", len(stale))
            return stale

    async def search_products(
        self,
        query: str,
        category: str = "",
        min_price: float = 0,
        max_price: float = 0,
        min_revenue: int = 0,
        min_items_sold: int = 0,
        min_rating: float = 0,
        min_commission: float = 0,
        revenue_growth_min: float = 0,
        is_affiliate: bool | None = None,
        sort_by: str = "revenue",
        region: str = "US",
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """Search products with filters. Checks cache first, then Apify."""

        async with async_session_factory() as db:
            q = select(TrendingProduct).where(
                TrendingProduct.expires_at > datetime.utcnow(),
                TrendingProduct.region == region,
            )

            if query:
                q = q.where(TrendingProduct.title.ilike(f"%{query}%"))
            if category:
                q = q.where(or_(
                    TrendingProduct.category.ilike(f"%{category}%"),
                    TrendingProduct.subcategory.ilike(f"%{category}%"),
                ))
            if min_price > 0:
                q = q.where(TrendingProduct.current_price >= min_price)
            if max_price > 0:
                q = q.where(TrendingProduct.current_price <= max_price)
            if min_revenue > 0:
                q = q.where(TrendingProduct.revenue_cents >= min_revenue * 100)
            if min_items_sold > 0:
                q = q.where(TrendingProduct.items_sold >= min_items_sold)
            if min_rating > 0:
                q = q.where(TrendingProduct.rating >= min_rating)
            if min_commission > 0:
                q = q.where(TrendingProduct.commission_rate >= min_commission)
            if revenue_growth_min > 0:
                q = q.where(TrendingProduct.revenue_growth_rate >= revenue_growth_min)
            if is_affiliate is True:
                q = q.where(TrendingProduct.is_affiliate.is_(True))
            elif is_affiliate is False:
                q = q.where(TrendingProduct.is_affiliate.is_(False))

            # Sort
            sort_map = {
                "revenue": TrendingProduct.revenue_cents.desc(),
                "items_sold": TrendingProduct.items_sold.desc(),
                "rating": TrendingProduct.rating.desc(),
                "price": TrendingProduct.current_price.asc(),
                "newest": TrendingProduct.created_at.desc(),
                "growth": TrendingProduct.revenue_growth_rate.desc(),
                "commission": TrendingProduct.commission_rate.desc(),
            }
            q = q.order_by(sort_map.get(sort_by, TrendingProduct.revenue_cents.desc()))

            # Count
            count_q = select(func.count()).select_from(q.subquery())
            total = await db.scalar(count_q) or 0

            # Paginate
            q = q.offset((page - 1) * per_page).limit(per_page)
            result = await db.execute(q)
            products = result.scalars().all()

            # Only use cache shortcut when:
            # (a) we have >= 3 pages worth of results (enough for infinite scroll), OR
            # (b) we are already on page 2+ (can't re-trigger Apify at that point)
            cache_is_sufficient = total >= per_page * 3
            if products and (cache_is_sufficient or page > 1):
                end = (page - 1) * per_page + len(products)
                return {
                    "products": [self._serialize(p) for p in products],
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "has_more": end < total,
                    "source": "cache",
                }
            elif products and not cache_is_sufficient and page == 1:
                # Cache exists but is too small — fall through to Apify to backfill
                logger.info(
                    "Cache has only %d rows (< %d needed for 3 pages), triggering fresh fetch",
                    total, per_page * 3,
                )

        # If cache empty for this query, try Apify search using existing scraper
        if (query or category) and self.api_token:
            try:
                search_str = query or category  # category-only → use category name as search
                raw_products = await self._run_apify_search(search_str, max_items=200)
                await self._cache_products(raw_products, section="search", region=region)

                # Re-query from cache with ALL filters (non-recursive, just re-runs the DB query)
                async with async_session_factory() as db:
                    q2 = select(TrendingProduct).where(
                        TrendingProduct.expires_at > datetime.utcnow(),
                        TrendingProduct.region == region,
                    )
                    if query:
                        q2 = q2.where(TrendingProduct.title.ilike(f"%{query}%"))
                    if category:
                        q2 = q2.where(or_(
                            TrendingProduct.category.ilike(f"%{category}%"),
                            TrendingProduct.subcategory.ilike(f"%{category}%"),
                        ))
                    if min_price > 0:
                        q2 = q2.where(TrendingProduct.current_price >= min_price)
                    if max_price > 0:
                        q2 = q2.where(TrendingProduct.current_price <= max_price)
                    if min_revenue > 0:
                        q2 = q2.where(TrendingProduct.revenue_cents >= min_revenue * 100)
                    if min_items_sold > 0:
                        q2 = q2.where(TrendingProduct.items_sold >= min_items_sold)
                    if min_rating > 0:
                        q2 = q2.where(TrendingProduct.rating >= min_rating)
                    if min_commission > 0:
                        q2 = q2.where(TrendingProduct.commission_rate >= min_commission)
                    if revenue_growth_min > 0:
                        q2 = q2.where(TrendingProduct.revenue_growth_rate >= revenue_growth_min)
                    if is_affiliate is True:
                        q2 = q2.where(TrendingProduct.is_affiliate.is_(True))
                    elif is_affiliate is False:
                        q2 = q2.where(TrendingProduct.is_affiliate.is_(False))
                    q2 = q2.order_by(sort_map.get(sort_by, TrendingProduct.revenue_cents.desc()))
                    count_q2 = select(func.count()).select_from(q2.subquery())
                    total2 = await db.scalar(count_q2) or 0
                    q2 = q2.offset((page - 1) * per_page).limit(per_page)
                    result2 = await db.execute(q2)
                    products2 = result2.scalars().all()
                    end2 = (page - 1) * per_page + len(products2)
                    return {
                        "products": [self._serialize(p) for p in products2],
                        "total": total2,
                        "page": page,
                        "per_page": per_page,
                        "has_more": end2 < total2,
                        "source": "apify",
                    }
            except Exception as e:
                logger.error("Apify search failed: %s", e)

        return {"products": [], "total": 0, "page": page, "per_page": per_page, "has_more": False, "source": "empty"}

    async def _run_parseforge_trending(self) -> list[dict]:
        """Run parseforge/tiktok-shop-scraper. Returns all sections in one call."""
        actor_id = "parseforge~tiktok-shop-scraper"
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items",
                    params={"token": self.api_token, "format": "json"},
                    json={"maxItems": 200},
                )
                resp.raise_for_status()
                items = resp.json()
                logger.info("parseforge returned %d items", len(items))

                try:
                    from services.usage_logger import log_api_usage
                    await log_api_usage(
                        user_id="",
                        service="apify_scrape",
                        operation="parseforge_trending",
                        cost_cents=max(1, len(items) // 10),
                    )
                except Exception:
                    pass

                # Enrich with detail images/video from pro100chok
                try:
                    items = await self._enrich_with_detail_images(items)
                except Exception as e:
                    logger.warning("Detail enrichment pass failed: %s", e)

                return items
        except Exception as e:
            logger.warning("parseforge trending failed: %s. Returning empty results.", e)
            return []

    async def _run_apify_search(self, query: str, max_items: int = 200) -> list[dict]:
        """Run the existing pro100chok~tiktok-shop-scraper for keyword search."""
        actor_id = "pro100chok~tiktok-shop-scraper"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{APIFY_BASE}/acts/{actor_id}/runs",
                params={"token": self.api_token},
                json={"queries": [query], "maxItems": max_items},
            )
            resp.raise_for_status()
            run_data = resp.json()["data"]
            run_id = run_data["id"]
            dataset_id = run_data["defaultDatasetId"]

            for _ in range(24):
                await asyncio.sleep(5)
                status_resp = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    params={"token": self.api_token},
                )
                status = status_resp.json()["data"]["status"]
                if status == "SUCCEEDED":
                    break
                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise RuntimeError(f"Apify run {run_id} failed: {status}")

            items_resp = await client.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items",
                params={"token": self.api_token, "format": "json"},
            )
            products = items_resp.json()
            if len(products) < 20:
                logger.warning(
                    "Apify returned only %d products — trial limit may be active. "
                    "Upgrade parseforge/tiktok-shop-scraper to paid plan for full results.",
                    len(products),
                )
            return products

    async def _enrich_with_detail_images(self, products: list[dict]) -> list[dict]:
        """Enrich parseforge results with additional images via pro100chok keyword search.

        parseforge returns only imageUrl (singular). pro100chok returns imageUrls (array).
        Strategy: search pro100chok by product title, match results back by word-overlap
        scoring (>= 2 shared significant words = match). This handles title variants
        across different scrapers.
        """
        if not self.api_token:
            return products

        needs_enrichment = [
            p for p in products
            if not p.get("imageUrls") and not p.get("images") and p.get("title")
        ]
        if not needs_enrichment:
            return products

        import re as _re

        def _words(s: str) -> set:
            """Extract significant words (len>=4) from title."""
            stopwords = {"with", "for", "the", "and", "inch", "pack", "size", "color",
                         "black", "white", "blue", "gray", "grey", "pink", "from", "this"}
            return {w for w in _re.sub(r"[^a-z0-9 ]", "", s.lower()).split()
                    if len(w) >= 4 and w not in stopwords}

        def _overlap_score(a: str, b: str) -> int:
            return len(_words(a) & _words(b))

        enriched_count = 0
        # Search each unique brand/product title individually — cap at 15 calls
        searched = 0
        for p in needs_enrichment:
            if searched >= 15:
                break
            title = p.get("title", "")[:80]
            # Use first 4 significant words as query (brand + product type)
            sig_words = list(_words(title))[:4]
            query = " ".join(sig_words) if sig_words else title[:40]
            if not query:
                continue

            try:
                detail_items = await self._run_apify_search(query, max_items=10)
                best_score = 0
                best_item = None
                for item in detail_items:
                    item_title = item.get("title") or ""
                    score = _overlap_score(title, item_title)
                    if score >= 2 and score > best_score:
                        best_score = score
                        best_item = item

                if best_item:
                    images = best_item.get("imageUrls") or best_item.get("images") or []
                    video = best_item.get("videoUrl") or best_item.get("demoVideo") or ""
                    video_list = best_item.get("videoUrls") or []
                    all_videos = ([video] if video else []) + [
                        v for v in (video_list if isinstance(video_list, list) else []) if v
                    ]
                    if images:
                        p["imageUrls"] = images
                        if all_videos:
                            p["videoUrls"] = all_videos
                        enriched_count += 1
                        logger.debug(
                            "Enriched '%s' via '%s' (overlap=%d, %d images)",
                            title[:40], best_item.get("title", "")[:40], best_score, len(images),
                        )
            except Exception as e:
                logger.warning("Enrichment search failed for '%s': %s", query, e)

            searched += 1

        logger.info(
            "Enriched %d/%d products with multi-image data via pro100chok (searched %d)",
            enriched_count, len(needs_enrichment), searched,
        )
        return products

    async def _cache_products(self, raw_products: list[dict], section: str, region: str):
        """Parse raw product data and cache in trending_products table."""
        expires = datetime.utcnow() + timedelta(hours=24)

        async with async_session_factory() as db:
            for i, raw in enumerate(raw_products):
                tiktok_id = str(raw.get("productId") or raw.get("id") or raw.get("product_id") or "")
                if not tiktok_id:
                    continue

                existing = await db.scalar(
                    select(TrendingProduct).where(TrendingProduct.tiktok_product_id == tiktok_id)
                )

                if existing:
                    tp = existing
                else:
                    tp = TrendingProduct(id=f"tp_{uuid4().hex[:12]}", tiktok_product_id=tiktok_id)
                    db.add(tp)

                tp.title = raw.get("title") or raw.get("name") or raw.get("productName") or ""
                tp.description = raw.get("description") or ""
                tp.product_url = raw.get("url") or raw.get("productUrl") or raw.get("product_url") or ""

                price = raw.get("price") or raw.get("currentPrice") or raw.get("salePrice") or raw.get("current_price") or 0
                if isinstance(price, str):
                    price = float(price.replace("$", "").replace(",", "").strip() or 0)
                tp.current_price = float(price)

                orig_price = raw.get("originalPrice") or raw.get("listPrice") or raw.get("original_price") or 0
                if isinstance(orig_price, str):
                    orig_price = float(orig_price.replace("$", "").replace(",", "").strip() or 0)
                tp.original_price = float(orig_price)

                if tp.original_price > 0 and tp.current_price > 0:
                    tp.discount_percent = round((1 - tp.current_price / tp.original_price) * 100, 1)

                tp.items_sold = int(raw.get("soldCount") or raw.get("itemsSold") or raw.get("salesVolume") or raw.get("sold") or raw.get("sales_volume") or 0)
                rev = int(tp.current_price * tp.items_sold * 100) if tp.items_sold else 0
                tp.revenue_cents = min(rev, 2_147_483_647)  # cap at int32 max
                tp.avg_unit_price = tp.current_price
                tp.revenue_growth_rate = float(raw.get("revenueGrowthRate") or 0)

                # Revenue trend sparkline — synthesize since parseforge has no historical data
                # (Task 0 confirmed: parseforge returns no revenueTrend / revenue30d fields)
                if tp.revenue_cents > 0:
                    tp.revenue_trend = _synthesize_revenue_trend(tp.revenue_cents, tp.revenue_growth_rate)
                    tp.revenue_trend_source = "synthetic"
                else:
                    tp.revenue_trend = []
                    tp.revenue_trend_source = "empty"

                tp.rating = float(raw.get("rating") or raw.get("averageRating") or raw.get("stars") or 0)
                tp.review_count = int(raw.get("reviewCount") or raw.get("reviews") or 0)

                tp.seller_name = raw.get("sellerName") or raw.get("shopName") or raw.get("seller_name") or ""
                tp.seller_id = str(raw.get("sellerId") or raw.get("shopId") or "")

                comm = raw.get("commissionRate") or raw.get("commission_rate") or 0
                if isinstance(comm, str):
                    comm = float(comm.replace("%", "").strip() or 0)
                comm = float(comm)
                # If > 1, assume it's a percentage (e.g. 15 means 15%)
                tp.commission_rate = comm / 100 if comm > 1 else comm
                tp.is_affiliate = tp.commission_rate > 0

                cat_raw = raw.get("category") or raw.get("categoryName") or ""
                if " > " in cat_raw:
                    parts = cat_raw.split(" > ")
                    tp.category = parts[0].strip()
                    tp.subcategory = parts[-1].strip() if len(parts) > 1 else ""
                else:
                    tp.category = cat_raw
                    tp.subcategory = raw.get("subcategory") or ""

                cover = raw.get("coverUrl") or raw.get("imageUrl") or raw.get("thumbnail") or ""
                all_images = raw.get("imageUrls") or raw.get("images") or raw.get("image_urls") or []
                if isinstance(cover, list):
                    all_images = cover
                    cover = cover[0] if cover else ""
                elif isinstance(all_images, str):
                    all_images = [all_images]
                tp.cover_image_url = cover if isinstance(cover, str) else (cover[0] if cover else "")
                tp.additional_image_urls = all_images  # Store all image URLs

                # Video URLs from enrichment (videoUrls populated by _enrich_with_detail_images)
                video_urls = []
                video_url = raw.get("videoUrl") or raw.get("demoVideo") or raw.get("video_url") or ""
                if video_url and isinstance(video_url, str):
                    video_urls.append(video_url)
                raw_videos = raw.get("videoUrls") or raw.get("videos") or []
                if isinstance(raw_videos, list):
                    video_urls.extend([v for v in raw_videos if isinstance(v, str) and v])
                tp.video_urls = list(dict.fromkeys(video_urls)) if video_urls else None  # dedupe

                tp.section = section
                tp.rank_position = i + 1
                tp.region = region
                tp.expires_at = expires

                logger.debug(
                    "Cached %s: %d images, %d videos, cover=%s",
                    tiktok_id, len(all_images), len(video_urls),
                    bool(tp.cover_image_url),
                )

            await db.commit()

    async def _get_cached(self, section: str, region: str, limit: int) -> list[dict]:
        """Get cached trending products (only non-expired)."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(TrendingProduct)
                .where(
                    TrendingProduct.section == section,
                    TrendingProduct.region == region,
                    TrendingProduct.expires_at > datetime.utcnow(),
                )
                .order_by(TrendingProduct.rank_position)
                .limit(limit)
            )
            products = result.scalars().all()
            return [self._serialize(p) for p in products] if products else []

    async def _get_cached_stale(self, section: str, region: str, limit: int) -> list[dict]:
        """Get cached trending products INCLUDING expired ones (stale fallback)."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(TrendingProduct)
                .where(
                    TrendingProduct.section == section,
                    TrendingProduct.region == region,
                )
                .order_by(TrendingProduct.rank_position)
                .limit(limit)
            )
            products = result.scalars().all()
            return [self._serialize(p) for p in products] if products else []

    @staticmethod
    def _proxy_cover(raw_url: str) -> str:
        """Wrap TikTok CDN URLs in the proxy endpoint to avoid hotlink blocking."""
        if not raw_url:
            return ""
        if raw_url.startswith("/api/") or "media.luminacast.com" in raw_url:
            return raw_url
        return f"/api/discover/proxy-image?url={quote(raw_url, safe='')}"

    @staticmethod
    def _serialize(tp: TrendingProduct) -> dict:
        revenue = tp.revenue_cents or 0
        if revenue >= 100_000_00:  # $100k+
            rev_display = f"${revenue / 100_000_00:.2f}m"
        elif revenue >= 100_00:  # $100+
            rev_display = f"${revenue / 100_00:.1f}k"
        elif revenue > 0:
            rev_display = f"${revenue / 100:.0f}"
        else:
            rev_display = ""

        proxy = ProductDiscoveryService._proxy_cover

        return {
            "id": tp.id,
            "tiktok_product_id": tp.tiktok_product_id,
            "title": tp.title,
            "description": tp.description,
            "product_url": tp.product_url,
            "current_price": tp.current_price,
            "original_price": tp.original_price,
            "discount_percent": tp.discount_percent,
            "revenue_cents": revenue,
            "revenue_display": rev_display,
            "revenue_growth_rate": tp.revenue_growth_rate,
            "items_sold": tp.items_sold,
            "avg_unit_price": tp.avg_unit_price,
            "rating": tp.rating,
            "review_count": tp.review_count,
            "seller_name": tp.seller_name,
            "commission_rate": tp.commission_rate,
            "commission_display": f"{tp.commission_rate * 100:.0f}%" if tp.commission_rate else "",
            "is_affiliate": tp.is_affiliate,
            "creator_count": tp.creator_count,
            "category": tp.category,
            "subcategory": tp.subcategory,
            "cover_image_url": proxy(tp.cover_image_url),
            "cover_image_r2_key": tp.cover_image_r2_key,
            "additional_image_urls": [proxy(u) for u in (tp.additional_image_urls or [])],
            "section": tp.section,
            "rank_position": tp.rank_position,
            # revenue_trend removed (Phase 3.3 — PD-003)
            "video_urls": tp.video_urls or [],
            "region": tp.region,
        }

    async def enrich_product_detail(self, product_url: str) -> dict:
        """Run pro100chok in product mode for a single product URL.

        Returns the full enriched detail dict matching the verified STEP 1 schema.
        Returns empty dict on failure — caller treats as "no enrichment available".

        Schema notes (verified 2026-04-07):
        - imageUrls: list of strings
        - videoUrls: list of strings (may be empty)
        - variants: list of {variantId, name, price, originalPrice, discountPercent,
                              stockStatus, stockQuantity, imageUrl}
        - specifications: DICT {key: value} — normalise to list on ingest
        - sellingPoints: list of strings (often empty)
        - description: string
        - shopRating: string "4.5" — must cast to float
        - shopFollowers: string "1802" — must cast to int via _parse_compact_number
        - soldLast30Days: often null
        - storeSubScores: {name: {score, percentage}}
        """
        if not self.api_token:
            logger.warning("APIFY_API_TOKEN not set, cannot enrich product detail")
            return {}

        actor_id = "pro100chok~tiktok-shop-scraper"
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items",
                    params={"token": self.api_token},
                    json={
                        "scrapeType": "product",
                        "queries": [product_url],
                        "maxItems": 1,
                        "includeReviews": False,
                        "proxyConfiguration": {
                            "useApifyProxy": True,
                            "apifyProxyGroups": ["RESIDENTIAL"],
                            "apifyProxyCountry": "US",
                        },
                    },
                )
                if resp.status_code not in (200, 201):
                    logger.warning(
                        "pro100chok product enrichment HTTP %s for %s: %s",
                        resp.status_code, product_url, resp.text[:300],
                    )
                    return {}

                items = resp.json()
                if not items or not isinstance(items, list):
                    logger.warning("pro100chok returned empty/invalid for %s", product_url)
                    return {}

                return items[0]

        except httpx.TimeoutException:
            logger.warning("pro100chok enrichment timed out for %s", product_url)
            return {}
        except Exception as e:
            logger.warning("pro100chok enrichment failed for %s: %s", product_url, e)
            return {}

    async def enrich_trending_product(self, tp_id: str) -> bool:
        """Enrich an existing TrendingProduct row with rich detail from pro100chok.

        Idempotent — returns True if already enriched OR successfully enriched now.
        Returns False on any failure; caller proceeds with shallow data.

        Schema mismatch handled:
        - specifications comes back as a dict — normalised to [{name, value}] list here
        - shopRating / shopFollowers come back as strings — cast via _parse_compact_number
        """
        from datetime import datetime
        from models.trending_product import TrendingProduct

        async with async_session_factory() as db:
            tp = await db.get(TrendingProduct, tp_id)
            if not tp:
                return False

            if tp.enriched_at:
                return True  # Already enriched — idempotent

            if not tp.product_url:
                logger.info("Cannot enrich %s: no product_url", tp_id)
                return False

            detail = await self.enrich_product_detail(tp.product_url)
            if not detail:
                return False

            # Images
            image_urls = detail.get("imageUrls") or []
            if isinstance(image_urls, list) and image_urls:
                tp.cover_image_url = image_urls[0]
                tp.additional_image_urls = image_urls

            # Videos (always a list in product mode, may be empty)
            video_urls = detail.get("videoUrls") or []
            tp.video_urls = video_urls if isinstance(video_urls, list) else []

            # Variants
            tp.variants = detail.get("variants") or []

            # Specifications: DICT {key: value} → normalise to [{name, value}]
            raw_specs = detail.get("specifications") or {}
            if isinstance(raw_specs, dict):
                tp.specifications = [{"name": k, "value": v} for k, v in raw_specs.items()]
            elif isinstance(raw_specs, list):
                tp.specifications = raw_specs  # already normalised
            else:
                tp.specifications = []

            # Selling points (often empty)
            tp.selling_points = detail.get("sellingPoints") or []

            # Full description
            tp.full_description = detail.get("description") or ""

            # Sales velocity
            tp.sold_last_30_days = _parse_compact_number(detail.get("soldLast30Days"))

            # Seller block — shopRating and shopFollowers come as strings
            try:
                raw_rating = detail.get("shopRating")
                tp.shop_rating = float(raw_rating) if raw_rating else None
            except (ValueError, TypeError):
                tp.shop_rating = None

            tp.shop_followers = _parse_compact_number(detail.get("shopFollowers"))
            tp.store_sub_scores = detail.get("storeSubScores") or {}
            tp.experience_scores = detail.get("experienceScores") or {}
            tp.shop_identity_label = detail.get("shopIdentityLabel") or ""
            tp.ratings_breakdown = detail.get("ratingsBreakdown") or {}

            tp.enriched_at = datetime.utcnow()
            await db.commit()

            logger.info(
                "Enriched %s: images=%d, videos=%d, variants=%d, specs=%d, desc_len=%d",
                tp_id, len(image_urls), len(tp.video_urls),
                len(tp.variants), len(tp.specifications), len(tp.full_description or ""),
            )
            return True
