"""Product Discovery — trending products, search with filters, import to library."""

import hashlib
import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.product import Product
from models.trending_product import TrendingProduct
from models.user import User
from routers.auth import get_current_user
from services.product_categories import get_category_tree
from services.product_discovery import ProductDiscoveryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/discover", tags=["product-discovery"])


# ── DISABLED: bulk Apify scrapes were costing $241/month ──────────────────
# The Discover tab is hidden in the frontend. Product import is now
# URL-only via /api/products/from-url (paste TikTok / Amazon link).
# Disabled handlers below short-circuit with HTTPException 410 (Gone)
# so any straggler frontend code or external caller gets a clear
# 'this endpoint is gone' instead of silently invoking Apify.
# To re-enable: delete the `_DISCOVER_DISABLED` line at the top of each
# disabled handler AND restore the Discover tab in ProductLibrary.tsx.
_DISCOVER_DISABLED = HTTPException(
    410,
    "Discovery is disabled. Use POST /api/products/from-url to import a product by URL.",
)


@router.get("/trending")
async def get_trending(
    section: str = Query("top_selling"),
    region: str = Query("US"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    sort_by: str = Query("revenue"),
    force_refresh: bool = Query(False),
    user: User = Depends(get_current_user),
):
    raise _DISCOVER_DISABLED  # ── disabled to stop Apify billing
    """Trending products for the Discover home view."""
    service = ProductDiscoveryService()
    all_products = await service.fetch_trending(section=section, region=region, max_items=200, force_refresh=force_refresh)

    # Server-side sort
    sort_fns = {
        "revenue": lambda p: p.get("revenue_cents", 0),
        "items_sold": lambda p: p.get("items_sold", 0),
        "rating": lambda p: p.get("rating", 0),
        "price": lambda p: -(p.get("current_price", 0)),  # ascending
        "growth": lambda p: p.get("revenue_growth_rate", 0),
        "commission": lambda p: p.get("commission_rate", 0),
    }
    if sort_by in sort_fns:
        all_products.sort(key=sort_fns[sort_by], reverse=True)

    total = len(all_products)
    start = (page - 1) * per_page
    end = start + per_page
    page_products = all_products[start:end]

    return {
        "products": page_products,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_more": end < total,
        "section": section,
        "region": region,
    }


@router.get("/search")
async def search_products(
    q: str = Query(""),
    category: str = Query(""),
    min_price: float = Query(0),
    max_price: float = Query(0),
    min_revenue: int = Query(0),
    min_items_sold: int = Query(0),
    min_rating: float = Query(0),
    min_commission: float = Query(0),
    revenue_growth_min: float = Query(0),
    is_affiliate: Optional[bool] = Query(None),
    sort_by: str = Query("revenue"),
    region: str = Query("US"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    force_refresh: bool = Query(False),
    user: User = Depends(get_current_user),
):
    raise _DISCOVER_DISABLED  # ── disabled to stop Apify billing
    """Search products with Kalodata-style filters."""
    service = ProductDiscoveryService()
    return await service.search_products(
        query=q, category=category, min_price=min_price,
        max_price=max_price, min_revenue=min_revenue,
        min_items_sold=min_items_sold, min_rating=min_rating,
        min_commission=min_commission, revenue_growth_min=revenue_growth_min,
        is_affiliate=is_affiliate, sort_by=sort_by, region=region,
        page=page, per_page=per_page,
    )


@router.get("/categories")
async def get_categories(user: User = Depends(get_current_user)):
    raise _DISCOVER_DISABLED  # ── disabled to stop Apify billing
    """Return the TikTok Shop category tree."""
    return {"categories": get_category_tree()}


@router.post("/import/{tp_id}")
async def import_to_library(
    tp_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raise _DISCOVER_DISABLED  # ── disabled to stop Apify billing
    """Import a trending product into the user's library."""
    tp = await db.get(TrendingProduct, tp_id)
    if not tp:
        raise HTTPException(404, "Product not found in trending cache")

    # ENRICH FIRST — run pro100chok product mode if not already enriched
    # Falls back gracefully: if Apify is down/slow, we proceed with shallow data
    if not tp.enriched_at:
        from services.product_discovery import ProductDiscoveryService
        svc = ProductDiscoveryService()
        enriched = await svc.enrich_trending_product(tp_id)
        if enriched:
            await db.refresh(tp)
            logger.info("Enriched %s before import: images=%d, videos=%d, variants=%d",
                tp_id,
                len(tp.additional_image_urls or []),
                len(tp.video_urls or []),
                len(tp.variants or []),
            )

    # Check if already imported
    existing = await db.scalar(
        select(Product).where(
            Product.user_id == user.id,
            Product.tiktok_product_id == tp.tiktok_product_id,
        )
    )
    if existing:
        return {"product_id": existing.id, "message": "Already in your library", "already_imported": True}

    # Proxy cover image to R2
    cover_r2_key = ""
    if tp.cover_image_url:
        try:
            import httpx
            from services.r2_storage import get_r2_storage_service
            r2 = get_r2_storage_service()
            r2_key = f"products/covers/{tp.tiktok_product_id}.jpg"
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(tp.cover_image_url, headers={"Referer": ""})
                if resp.status_code == 200 and len(resp.content) > 500:
                    await r2.upload_bytes(resp.content, r2_key, "image/jpeg")
                    cover_r2_key = r2_key
        except Exception as e:
            logger.warning("Cover proxy failed for %s: %s", tp_id, e)

    product = Product(
        id=f"prod_{uuid.uuid4().hex[:12]}",
        user_id=user.id,
        tiktok_product_id=tp.tiktok_product_id,
        tiktok_product_url=tp.product_url,
        title=tp.title,
        name=tp.title,
        description=tp.description,
        full_description=tp.full_description,
        product_url=tp.product_url,
        price=tp.current_price,
        current_price=tp.current_price,
        original_price=tp.original_price,
        discount_percent=tp.discount_percent,
        commission_rate=tp.commission_rate or 0.15,
        sales_volume=tp.items_sold,
        sold_last_30_days=tp.sold_last_30_days,
        rating=tp.rating,
        review_count=tp.review_count,
        seller_name=tp.seller_name,
        category=tp.category,
        cover_image_key=cover_r2_key,
        # Rich pro100chok fields
        variants=tp.variants or [],
        specifications=tp.specifications or [],
        selling_points=tp.selling_points or [],
        shop_rating=tp.shop_rating,
        shop_followers=tp.shop_followers,
        shop_identity_label=tp.shop_identity_label or "",
        store_sub_scores=tp.store_sub_scores or {},
    )
    db.add(product)
    await db.flush()  # flush to get product.id for assets

    # Import additional images as ProductAsset records
    from models.product_asset import ProductAsset
    if tp.additional_image_urls:
        from services.r2_storage import get_r2_storage_service as _get_r2
        r2_svc = _get_r2()
        for idx, img_url in enumerate(tp.additional_image_urls[:8]):
            if img_url == tp.cover_image_url:
                continue  # Skip duplicate of cover
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(img_url, headers={"Referer": ""})
                    if resp.status_code == 200 and len(resp.content) > 500:
                        asset_key = f"products/{product.id}/assets/img_{idx}.jpg"
                        await r2_svc.upload_bytes(resp.content, asset_key, "image/jpeg")
                        asset = ProductAsset(
                            id=f"pa_{uuid.uuid4().hex[:12]}",
                            product_id=product.id,
                            user_id=user.id,
                            asset_type="imported_image",
                            media_type="image",
                            r2_key=asset_key,
                            r2_url=r2_svc.get_public_url(asset_key),
                            file_size_bytes=len(resp.content),
                        )
                        db.add(asset)
            except Exception as e:
                logger.warning("Failed to import additional image %d: %s", idx, e)

    # Import video assets if available
    if tp.video_urls:
        from services.r2_storage import get_r2_storage_service as _get_r2_v
        r2_v = _get_r2_v()
        for vidx, vid_url in enumerate(tp.video_urls[:3]):
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(vid_url, headers={"Referer": ""})
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        asset_key = f"products/{product.id}/assets/vid_{vidx}.mp4"
                        await r2_v.upload_bytes(resp.content, asset_key, "video/mp4")
                        asset = ProductAsset(
                            id=f"pa_{uuid.uuid4().hex[:12]}",
                            product_id=product.id,
                            user_id=user.id,
                            asset_type="imported_video",
                            media_type="video",
                            r2_key=asset_key,
                            r2_url=r2_v.get_public_url(asset_key),
                            file_size_bytes=len(resp.content),
                        )
                        db.add(asset)
            except Exception as e:
                logger.warning("Failed to import video asset %d: %s", vidx, e)

    # Count what was imported
    image_count = 0
    video_count = 0
    if tp.additional_image_urls:
        image_count = len([u for u in tp.additional_image_urls if u != tp.cover_image_url])
    if tp.video_urls:
        video_count = len(tp.video_urls[:3])

    await db.commit()

    logger.info(
        "Imported %s from %s: cover=%s, images=%d, videos=%d, variants=%d, specs=%d",
        product.id, tp_id, bool(cover_r2_key), image_count, video_count,
        len(tp.variants or []), len(tp.specifications or []),
    )

    return {
        "product_id": product.id,
        "message": "Added to your library",
        "already_imported": False,
        "import_summary": {
            "cover": bool(cover_r2_key),
            "additional_images": image_count,
            "videos": video_count,
            "variants": len(tp.variants or []),
            "specifications": len(tp.specifications or []),
            "selling_points": len(tp.selling_points or []),
        },
    }


_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\x9cc\xfa"
    b"\xcf\x00\x00\x00\x02\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


@router.get("/proxy-image")
async def proxy_product_image(url: str = Query(...)):
    """Proxy TikTok CDN images. Returns 502 on upstream failure so the
    frontend can show a real placeholder instead of a silent 1x1 PNG."""
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Invalid url")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Referer": ""})
            if resp.status_code != 200:
                logger.info("proxy-image upstream %d for %s", resp.status_code, url[:120])
                raise HTTPException(502, f"Upstream returned {resp.status_code}")
            if len(resp.content) > 5 * 1024 * 1024:
                raise HTTPException(502, "Upstream image too large")
            ct = resp.headers.get("content-type", "image/jpeg")
            if not ct.startswith("image/"):
                raise HTTPException(502, "Upstream not an image")
            return Response(
                content=resp.content,
                media_type=ct,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("proxy-image error for %s: %s", url[:120], e)
        raise HTTPException(502, "Proxy fetch failed")


@router.post("/backfill-trending-covers")
async def backfill_trending_covers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-scrape cover images for trending products that have no cover_image_url.
    Looks up product by tiktok_product_id from the Apify search scraper.
    """
    raise _DISCOVER_DISABLED  # ── disabled to stop Apify billing
    if user.role.value != "admin":
        raise HTTPException(403, "Admin only")

    from services.tiktok_shop import get_tiktok_shop_service
    service = get_tiktok_shop_service()

    result = await db.execute(
        select(TrendingProduct).where(
            TrendingProduct.cover_image_url == "",
        ).limit(100)
    )
    products = result.scalars().all()

    if not products:
        return {"message": "All trending products already have cover images", "updated": 0}

    # Group by unique titles and search for each
    updated = 0
    for tp in products:
        if not tp.title:
            continue
        try:
            # Search by first 3 words of the title
            search_terms = " ".join(tp.title.split()[:3])
            results = await service.search_products(search_terms, max_products=5)
            for r in results:
                if r.get("product_id") == tp.tiktok_product_id:
                    image_urls = r.get("image_urls", [])
                    if image_urls:
                        tp.cover_image_url = image_urls[0]
                        updated += 1
                        break
        except Exception as e:
            logger.warning("Backfill cover failed for %s: %s", tp.id, e)

    await db.commit()
    return {"updated": updated, "total_checked": len(products)}


@router.post("/refresh")
async def refresh_trending(
    section: str = Query("top_selling"),
    region: str = Query("US"),
    user: User = Depends(get_current_user),
):
    raise _DISCOVER_DISABLED  # ── disabled to stop Apify billing
    """Force refresh trending cache (admin only)."""
    if user.role.value != "admin":
        raise HTTPException(403, "Admin only")

    service = ProductDiscoveryService()
    products = await service.fetch_trending(
        section=section, region=region, max_items=200, force_refresh=True,
    )
    return {"refreshed": len(products), "section": section}
