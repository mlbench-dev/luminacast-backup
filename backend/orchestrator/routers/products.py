"""Product Library — CRUD, TikTok Shop import, URL import, asset management, AI generation."""

import logging
import os
import uuid
import hashlib
from typing import Optional, List
from urllib.parse import quote
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from pydantic import BaseModel
import sentry_sdk
from database import get_db
from models.user import User
from models.product import Product
from models.product_asset import ProductAsset
from models.cast import Cast, CastStatus, CastProduct
from models.block import Block
from routers.auth import get_current_user
from services import audit_log
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/products", tags=["products"])

# Cap on assets created per product import. Default 0 = no cap; import every
# asset the resolver returns. We previously capped at 10 to bound R2 storage
# blast radius, but that silently dropped assets users actually wanted.
#
# 2026-05-05: Storage-aware billing is the planned successor — once a user's
# monthly R2 storage cost exceeds $1, a cap kicks in and incremental storage
# is billed at cost + 30%. Until that ships, no cap. Set
# MAX_PRODUCT_ASSETS_PER_PRODUCT=N (positive int) to re-enable a hard cap.
_MAX_RAW = os.environ.get("MAX_PRODUCT_ASSETS_PER_PRODUCT", "0")
try:
    MAX_PRODUCT_ASSETS_PER_PRODUCT = int(_MAX_RAW)
except ValueError:
    MAX_PRODUCT_ASSETS_PER_PRODUCT = 0  # treat malformed env as no cap


# ── Schemas ──

class ProductCreate(BaseModel):
    name: str
    price: float
    commission_rate: Optional[float] = None
    description: Optional[str] = None
    tiktok_product_url: Optional[str] = None
    tiktok_product_id: Optional[str] = None
    media_keys: Optional[List[str]] = None
    current_price: float = 0
    original_price: float = 0
    discount_percent: float = 0
    sales_volume: int = 0
    rating: float = 0
    review_count: int = 0
    seller_name: str = ""
    category: str = ""
    tags: Optional[List[str]] = None
    cover_image_key: str = ""
    # Manual-entry fallback fields. When the URL importer can't reach a
    # source (e.g. TikTok Shop CAPTCHA), the frontend redirects the user to
    # the manual create form pre-filled with these so the product still
    # links back to its origin.
    source: Optional[str] = None
    source_url: Optional[str] = None
    source_product_id: Optional[str] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    commission_rate: Optional[float] = None
    description: Optional[str] = None
    tiktok_product_url: Optional[str] = None
    tiktok_product_id: Optional[str] = None
    media_keys: Optional[List[str]] = None
    current_price: Optional[float] = None
    original_price: Optional[float] = None
    discount_percent: Optional[float] = None
    sales_volume: Optional[int] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    seller_name: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    cover_image_key: Optional[str] = None


class ProductResponse(BaseModel):
    id: str
    name: str
    price: float
    commission_rate: Optional[float] = None
    commission_source: Optional[str] = None
    commission_category: Optional[str] = None
    affiliate_tag: Optional[str] = None
    description: Optional[str] = None
    tiktok_product_id: Optional[str] = None
    tiktok_product_url: Optional[str] = None
    current_price: float = 0
    original_price: float = 0
    discount_percent: float = 0
    sales_volume: int = 0
    rating: float = 0
    review_count: int = 0
    seller_name: str = ""
    category: str = ""
    tags: Optional[list] = None
    cover_image_key: str = ""
    media_keys: Optional[List[str]] = None
    overlay_key: Optional[str] = None
    status: str = "active"
    created_at: Optional[str] = None
    asset_count: int = 0
    video_count: int = 0
    image_count: int = 0

    model_config = {"from_attributes": True}


class AssetResponse(BaseModel):
    id: str
    asset_type: str
    media_type: str
    r2_key: str
    r2_url: str
    duration_seconds: float = 0
    width: int = 0
    height: int = 0
    file_size_bytes: int = 0
    generation_prompt: str = ""
    generation_model: str = ""
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


async def _product_to_response(product: Product, db: AsyncSession) -> dict:
    """Convert Product to response dict with asset counts."""
    # Count assets
    asset_counts = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(ProductAsset.media_type == "video").label("videos"),
            func.count().filter(ProductAsset.media_type == "image").label("images"),
        ).where(ProductAsset.product_id == product.id)
    )
    row = asset_counts.one()
    return {
        "id": product.id,
        "name": product.name,
        "price": product.price,
        "commission_rate": product.commission_rate,
        "commission_source": product.commission_source,
        "commission_category": product.commission_category,
        "affiliate_tag": product.affiliate_tag,
        "description": product.description,
        "tiktok_product_id": product.tiktok_product_id,
        "tiktok_product_url": product.tiktok_product_url,
        "current_price": product.current_price or 0,
        "original_price": product.original_price or 0,
        "discount_percent": product.discount_percent or 0,
        "sales_volume": product.sales_volume or 0,
        "rating": product.rating or 0,
        "review_count": product.review_count or 0,
        "seller_name": product.seller_name or "",
        "category": product.category or "",
        "tags": product.tags or [],
        "cover_image_key": product.cover_image_key or "",
        "media_keys": product.media_keys,
        "overlay_key": product.overlay_key,
        "status": product.status or "active",
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "asset_count": row.total,
        "video_count": row.videos,
        "image_count": row.images,
        "full_description": product.full_description or "",
        "variants": product.variants or [],
        "specifications": product.specifications or [],
        "selling_points": product.selling_points or [],
        "shop_rating": product.shop_rating,
        "shop_followers": product.shop_followers,
        "shop_identity_label": product.shop_identity_label or "",
        "store_sub_scores": product.store_sub_scores or {},
        "sold_last_30_days": product.sold_last_30_days,
    }


# ── CRUD ──

@router.post("", status_code=201)
async def create_product(
    req: ProductCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Manual-entry fallback: if the create came from the URL-import fallback
    # flow, the origin arrives as source_*; fold it into the TikTok / generic
    # link columns so the product still points back at where it came from.
    tiktok_product_url = req.tiktok_product_url
    tiktok_product_id = req.tiktok_product_id
    product_url = ""
    if req.source == "tiktok":
        tiktok_product_url = tiktok_product_url or req.source_url
        tiktok_product_id = tiktok_product_id or req.source_product_id
        product_url = req.source_url or ""
    elif req.source_url:
        product_url = req.source_url

    prod_id = f"prod_{uuid.uuid4().hex[:12]}"
    product = Product(
        id=prod_id,
        user_id=user.id,
        name=req.name,
        price=req.price,
        commission_rate=req.commission_rate,
        description=req.description,
        tiktok_product_url=tiktok_product_url,
        tiktok_product_id=tiktok_product_id,
        product_url=product_url,
        media_keys=req.media_keys,
        current_price=req.current_price,
        original_price=req.original_price,
        discount_percent=req.discount_percent,
        sales_volume=req.sales_volume,
        rating=req.rating,
        review_count=req.review_count,
        seller_name=req.seller_name,
        category=req.category,
        tags=req.tags or [],
        cover_image_key=req.cover_image_key,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    try:
        await audit_log.record(
            db, user_id=user.id, action="product.create", entity_type="product",
            entity_id=prod_id, after={"name": product.name, "price": product.price},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return await _product_to_response(product, db)


class FromUrlRequest(BaseModel):
    url: str


@router.post("/from-url", status_code=201)
async def import_from_url(
    req: FromUrlRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a product from any URL (TikTok, Amazon, or generic e-commerce).

    De-duplicates by source_product_id (TikTok) or product_url (others).
    Returns existing product if already imported.
    """
    from services.url_product_resolver import resolve_product_url, TikTokBlockedError, GenericSiteBlockedError

    try:
        resolved = await resolve_product_url(req.url)
    except TikTokBlockedError as exc:
        sentry_sdk.capture_exception(exc)
        logger.info(
            "[from-url-blocked] returning manual-entry fallback url=%s product_id=%s",
            exc.source_url, exc.source_product_id,
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "needs_manual_entry",
                "source": "tiktok",
                "source_product_id": exc.source_product_id,
                "source_url": exc.source_url,
                "message": (
                    "We couldn't fetch this TikTok Shop product automatically. "
                    "Please enter the product details and upload a cover image manually."
                ),
            },
        )
    except GenericSiteBlockedError as exc:
        sentry_sdk.capture_exception(exc)
        logger.info(
            "[from-url-blocked] returning manual-entry fallback url=%s reason=%s",
            exc.source_url, exc.reason,
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "needs_manual_entry",
                "source": "generic",
                "source_product_id": None,
                "source_url": exc.source_url,
                "message": exc.reason,
            },
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(400, f"Could not resolve product from URL: {str(exc)[:200]}")

    # De-duplicate: check if user already has this product
    existing = None
    if resolved.source == "tiktok" and resolved.source_product_id:
        existing = await db.scalar(
            select(Product).where(
                Product.user_id == user.id,
                Product.tiktok_product_id == resolved.source_product_id,
            )
        )
    elif resolved.source_url:
        existing = await db.scalar(
            select(Product).where(
                Product.user_id == user.id,
                Product.product_url == resolved.source_url,
            )
        )

    if existing:
        resp = await _product_to_response(existing, db)
        resp["already_existed"] = True
        if existing.cover_image_key:
            resp["cover_image_url"] = f"{settings.R2_PUBLIC_URL}/{existing.cover_image_key}"
        return resp

    # Download cover image to R2
    cover_image_key = ""
    if resolved.cover_image_url:
        try:
            import httpx as _httpx
            from services.r2_storage import get_r2_storage_service
            r2 = get_r2_storage_service()
            async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                img_resp = await client.get(resolved.cover_image_url, headers={"Referer": ""})
                if img_resp.status_code == 200 and len(img_resp.content) > 500:
                    url_hash = hashlib.md5(resolved.cover_image_url.encode()).hexdigest()[:12]
                    cover_image_key = f"products/covers/{url_hash}.jpg"
                    await r2.upload_bytes(img_resp.content, cover_image_key, "image/jpeg")
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("Failed to download cover image: %s", exc)

    # Resolve commission per source.  TikTok extracts the real rate from the
    # resolver payload; Amazon looks up the published rate by category;
    # everything else stays null until the user enters a value.
    commission_rate: Optional[float] = None
    commission_source: Optional[str] = None
    commission_category: Optional[str] = None

    if resolved.source == "tiktok":
        commission_source = "tiktok_affiliate"
        raw = getattr(resolved, "raw", None) or {}
        rate_raw = (
            raw.get("commissionRate")
            or raw.get("commission_rate")
            or raw.get("commissionPercent")
            or raw.get("commission")
        )
        if isinstance(rate_raw, (int, float)):
            commission_rate = float(rate_raw) / 100 if rate_raw > 1 else float(rate_raw)
        elif isinstance(rate_raw, str):
            import re as _re
            nums = _re.findall(r"[\d.]+", rate_raw)
            if nums:
                v = float(nums[0])
                commission_rate = v / 100 if v > 1 else v
    elif resolved.source == "amazon":
        from services.amazon_commission import get_amazon_commission
        comm = get_amazon_commission(resolved.category or "", resolved.price or 0)
        commission_rate = comm["rate"]
        commission_source = "amazon_associates"
        commission_category = comm["category_matched"]
    else:
        commission_source = "manual"

    prod_id = f"prod_{uuid.uuid4().hex[:12]}"
    product = Product(
        id=prod_id,
        user_id=user.id,
        name=resolved.title or "Imported Product",
        title=resolved.title,
        description=resolved.description or "",
        price=resolved.price or 0,
        current_price=resolved.price or 0,
        original_price=resolved.original_price or 0,
        product_url=resolved.source_url or req.url,
        tiktok_product_id=resolved.source_product_id if resolved.source == "tiktok" else None,
        tiktok_product_url=resolved.source_url if resolved.source == "tiktok" else None,
        cover_image_key=cover_image_key,
        seller_name=resolved.seller_name or "",
        rating=resolved.rating or 0,
        review_count=resolved.review_count or 0,
        category=resolved.category or "",
        variants=resolved.variants,
        specifications=resolved.specifications,
        commission_rate=commission_rate,
        commission_source=commission_source,
        commission_category=commission_category,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)

    # ── Download ALL product media to R2 and create ProductAsset rows ──
    # The cover was already uploaded above (cover_image_key). This block
    # downloads the remaining `resolved.media_urls` in parallel and writes
    # one ProductAsset per media item so the gallery / carousel can show
    # the full media set instead of just the cover.
    try:
        await _materialize_product_assets(
            db=db,
            user=user,
            product_id=prod_id,
            cover_image_key=cover_image_key,
            cover_image_url=resolved.cover_image_url or "",
            media_urls=list(resolved.media_urls or []),
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("product_import.media_download_failed: %s", exc)

    try:
        from services.cost_rates import COST_RATES
        from services.usage_tracker import log_usage
        await log_usage(
            db,
            user_id=user.id,
            event_type="product_import",
            provider="apify",
            provider_cost_usd=float(COST_RATES.get("apify/single_url_scrape", 0.05)),
            quantity=1,
            quantity_unit="calls",
            resource_type="product",
            resource_id=product.id,
        )
        await db.commit()
    except Exception as _exc:
        sentry_sdk.capture_exception(_exc)

    resp = await _product_to_response(product, db)
    resp["already_existed"] = False
    if cover_image_key:
        resp["cover_image_url"] = f"{settings.R2_PUBLIC_URL}/{cover_image_key}"
    return resp


async def _materialize_product_assets(
    *,
    db: AsyncSession,
    user: User,
    product_id: str,
    cover_image_key: str,
    cover_image_url: str,
    media_urls: List[str],
) -> int:
    """Download all media URLs to R2 and create ProductAsset rows.

    Idempotent: if any ProductAsset already exists for this product_id,
    skip the whole operation (re-import handler returns the existing
    product before this is called, but a retry of a half-finished import
    could land here twice).
    """
    if not media_urls and not cover_image_key:
        return 0

    existing_count = await db.scalar(
        select(func.count()).select_from(ProductAsset).where(
            ProductAsset.product_id == product_id
        )
    )
    if existing_count and existing_count > 0:
        logger.info(
            "product_import.assets_already_exist product=%s count=%s",
            product_id, existing_count,
        )
        return 0

    import asyncio
    import httpx as _httpx
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    async def _download_one(media_url: str, index: int) -> Optional[ProductAsset]:
        try:
            async with _httpx.AsyncClient(
                timeout=30, follow_redirects=True,
            ) as dl_client:
                resp = await dl_client.get(media_url, headers={"Referer": ""})
            if resp.status_code != 200 or len(resp.content) < 500:
                return None

            content = resp.content
            ct = resp.headers.get("content-type", "image/jpeg")
            if "video" in ct:
                media_type = "video"
                ext = "mp4"
            elif "gif" in ct:
                media_type = "image"
                ext = "gif"
            else:
                media_type = "image"
                ext = "jpg"

            asset_id = f"pa_{uuid.uuid4().hex[:12]}"
            r2_key = f"products/{product_id}/assets/{asset_id}.{ext}"
            await r2.upload_bytes(content, r2_key, ct)

            return ProductAsset(
                id=asset_id,
                product_id=product_id,
                user_id=user.id,
                asset_type="gallery",
                media_type=media_type,
                r2_key=r2_key,
                r2_url=r2.get_public_url(r2_key),
                file_size_bytes=len(content),
                position=index,
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "product_import.asset_download_failed index=%s url=%s: %s",
                index, media_url[:80], exc,
            )
            return None

    # Skip the cover URL (already in R2) and apply the optional cap.
    other_urls = [u for u in media_urls if u and u != cover_image_url]
    if MAX_PRODUCT_ASSETS_PER_PRODUCT > 0:
        # Cap is set — keep room for the cover row at position 0.
        extras_cap = max(0, MAX_PRODUCT_ASSETS_PER_PRODUCT - 1)
        other_urls = other_urls[:extras_cap]

    assets_to_add: List[ProductAsset] = []

    # Cover asset row — bytes already uploaded earlier, so just create the
    # DB row pointing at the existing R2 key. Use HEAD to recover the file
    # size rather than storing 0 (downstream code may filter on size).
    if cover_image_key:
        cover_size = 0
        try:
            head = await r2.head_object(cover_image_key)
            if head and "ContentLength" in head:
                cover_size = int(head["ContentLength"])
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "product_import.cover_head_failed key=%s: %s",
                cover_image_key, exc,
            )
        assets_to_add.append(ProductAsset(
            id=f"pa_{uuid.uuid4().hex[:12]}",
            product_id=product_id,
            user_id=user.id,
            asset_type="gallery",
            media_type="image",
            r2_key=cover_image_key,
            r2_url=r2.get_public_url(cover_image_key),
            file_size_bytes=cover_size,
            position=0,
        ))

    if other_urls:
        tasks = [_download_one(url, i + 1) for i, url in enumerate(other_urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, ProductAsset):
                assets_to_add.append(r)

    if not assets_to_add:
        return 0

    for a in assets_to_add:
        db.add(a)
    await db.commit()
    logger.info(
        "product_import.assets_created product=%s count=%d",
        product_id, len(assets_to_add),
    )
    return len(assets_to_add)


@router.post("/{product_id}/refresh")
async def refresh_product(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run the URL resolver for an existing product and backfill any
    missing fields (cover image, price, rating, seller, description,
    category, variants, specs).

    Used for legacy rows that were imported when the resolver had a
    schema mismatch with the actor (e.g. early Amazon imports where the
    junglee actor's nested price/seller shape was misread). Existing
    user-edited fields are preserved — we only fill blanks / zeroes.
    """
    from services.url_product_resolver import resolve_product_url

    product = await db.scalar(
        select(Product).where(Product.id == product_id, Product.user_id == user.id)
    )
    if not product:
        raise HTTPException(404, "Product not found")

    target_url = product.product_url or product.tiktok_product_url
    if not target_url:
        raise HTTPException(400, "Product has no source URL to refresh from")

    try:
        resolved = await resolve_product_url(target_url)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(400, f"Could not refresh product: {str(exc)[:200]}")

    # Backfill ONLY when the existing field is blank / zero. Never
    # overwrite a value the user may have edited in place.
    if (not product.name or product.name == "Imported Product") and resolved.title:
        product.name = resolved.title
    if (not product.title) and resolved.title:
        product.title = resolved.title
    if (not product.description) and resolved.description:
        product.description = resolved.description
    if (not product.price or product.price == 0) and resolved.price:
        product.price = resolved.price
        product.current_price = resolved.price
    if (not product.original_price or product.original_price == 0) and resolved.original_price:
        product.original_price = resolved.original_price
    if (not product.seller_name) and resolved.seller_name:
        product.seller_name = resolved.seller_name
    if (not product.rating or product.rating == 0) and resolved.rating:
        product.rating = resolved.rating
    if (not product.review_count or product.review_count == 0) and resolved.review_count:
        product.review_count = resolved.review_count
    if (not product.category) and resolved.category:
        product.category = resolved.category
    if (not product.variants) and resolved.variants:
        product.variants = resolved.variants
    if (not product.specifications) and resolved.specifications:
        product.specifications = resolved.specifications

    # Cover image: only fetch if the product has no key yet AND the
    # resolver returned an image URL we can download.
    if (not product.cover_image_key) and resolved.cover_image_url:
        try:
            import httpx as _httpx
            from services.r2_storage import get_r2_storage_service
            r2 = get_r2_storage_service()
            async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                img_resp = await client.get(
                    resolved.cover_image_url, headers={"Referer": ""}
                )
                if img_resp.status_code == 200 and len(img_resp.content) > 500:
                    url_hash = hashlib.md5(
                        resolved.cover_image_url.encode()
                    ).hexdigest()[:12]
                    cover_image_key = f"products/covers/{url_hash}.jpg"
                    await r2.upload_bytes(img_resp.content, cover_image_key, "image/jpeg")
                    product.cover_image_key = cover_image_key
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("refresh_product cover download failed: %s", exc)

    await db.commit()
    await db.refresh(product)

    resp = await _product_to_response(product, db)
    if product.cover_image_key:
        resp["cover_image_url"] = f"{settings.R2_PUBLIC_URL}/{product.cover_image_key}"
    return resp


@router.get("")
async def list_products(
    page: int = Query(1, ge=1),
    per_page: int = Query(16, ge=1, le=100),
    search: str = Query("", max_length=200),
    sort: str = Query("newest"),
    filter: str = Query("all"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Product).where(
        Product.user_id == user.id,
        or_(Product.deleted_at.is_(None), Product.status != "deleted"),
    )

    if search:
        query = query.where(
            or_(
                Product.name.ilike(f"%{search}%"),
                Product.description.ilike(f"%{search}%"),
                Product.category.ilike(f"%{search}%"),
            )
        )

    # Sorting
    if sort == "newest":
        query = query.order_by(Product.created_at.desc())
    elif sort == "created_asc":
        query = query.order_by(Product.created_at.asc())
    elif sort == "price_asc":
        query = query.order_by(Product.price.asc())
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc())
    elif sort == "best_selling":
        query = query.order_by(Product.sales_volume.desc())
    elif sort == "highest_rated":
        query = query.order_by(Product.rating.desc())
    elif sort == "name_asc":
        query = query.order_by(Product.name.asc())
    elif sort == "commission_desc":
        # NULLS LAST so unset / manual products sink to the bottom of
        # "highest commission %" instead of bubbling above paid sources.
        query = query.order_by(Product.commission_rate.desc().nulls_last())
    elif sort == "commission_asc":
        query = query.order_by(Product.commission_rate.asc().nulls_last())
    elif sort == "commission_amount_desc":
        # Effective commission = rate * price. SQL evaluates NULL * x as
        # NULL, so nulls_last() drops unset rates to the bottom too.
        query = query.order_by(
            (Product.commission_rate * Product.price).desc().nulls_last()
        )
    else:
        query = query.order_by(Product.created_at.desc())

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    products = result.scalars().all()

    # Batch lookup TikTok CDN fallback URLs for products missing covers
    from models.trending_product import TrendingProduct
    missing_cover_tiktok_ids = [
        p.tiktok_product_id for p in products
        if not p.cover_image_key and p.tiktok_product_id
    ]
    tiktok_cover_map: dict[str, str] = {}
    if missing_cover_tiktok_ids:
        tp_result = await db.execute(
            select(TrendingProduct.tiktok_product_id, TrendingProduct.cover_image_url).where(
                TrendingProduct.tiktok_product_id.in_(missing_cover_tiktok_ids),
                TrendingProduct.cover_image_url != "",
            )
        )
        for row in tp_result.all():
            tiktok_cover_map[row.tiktok_product_id] = row.cover_image_url

    items = []
    for p in products:
        resp = await _product_to_response(p, db)
        if p.cover_image_key:
            resp["cover_image_url"] = f"{settings.R2_PUBLIC_URL}/{p.cover_image_key}"
        elif p.tiktok_product_id and p.tiktok_product_id in tiktok_cover_map:
            # Fallback: proxy TikTok CDN image (URL-encode to handle ? and & in CDN URLs)
            resp["cover_image_url"] = f"/api/discover/proxy-image?url={quote(tiktok_cover_map[p.tiktok_product_id], safe='')}"
        else:
            resp["cover_image_url"] = ""
        items.append(resp)

    # Filter by assets if needed
    if filter == "with_video":
        items = [i for i in items if i["video_count"] > 0]
    elif filter == "missing_assets":
        items = [i for i in items if i["asset_count"] == 0]

    return {"products": items, "total": total, "page": page, "per_page": per_page}


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    resp = await _product_to_response(product, db)

    # Cover image URL — with TikTok CDN fallback (URL-encoded)
    from models.trending_product import TrendingProduct
    if product.cover_image_key:
        resp["cover_image_url"] = f"{settings.R2_PUBLIC_URL}/{product.cover_image_key}"
    elif product.tiktok_product_id:
        tp = await db.scalar(
            select(TrendingProduct).where(
                TrendingProduct.tiktok_product_id == product.tiktok_product_id,
                TrendingProduct.cover_image_url != "",
            )
        )
        resp["cover_image_url"] = (
            f"/api/discover/proxy-image?url={quote(tp.cover_image_url, safe='')}" if tp and tp.cover_image_url else ""
        )
    else:
        resp["cover_image_url"] = ""

    # Product URL (TikTok link)
    resp["product_url"] = product.product_url or product.tiktok_product_url or ""

    # Include assets, ordered for gallery display: position asc, then created_at asc
    assets_result = await db.execute(
        select(ProductAsset).where(ProductAsset.product_id == product_id)
        .order_by(ProductAsset.position.asc(), ProductAsset.created_at.asc())
    )
    assets = assets_result.scalars().all()
    resp["assets"] = [
        {
            "id": a.id,
            "asset_type": a.asset_type,
            "media_type": a.media_type,
            "r2_key": a.r2_key,
            "r2_url": a.r2_url or "",
            "position": a.position or 0,
            "duration_seconds": a.duration_seconds or 0,
            "width": a.width or 0,
            "height": a.height or 0,
            "file_size_bytes": a.file_size_bytes or 0,
            "generation_prompt": a.generation_prompt or "",
            "generation_model": a.generation_model or "",
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in assets
    ]

    # Assets status summary
    resp["assets_status"] = {
        "has_cover": bool(product.cover_image_key),
        "image_count": sum(1 for a in assets if a.media_type == "image"),
        "video_count": sum(1 for a in assets if a.media_type == "video"),
        "has_video": any(a.media_type == "video" for a in assets),
    }

    # Casts using this product (via blocks or cast_products)
    casts_via_blocks = await db.execute(
        select(Cast.id, Cast.name, Cast.status)
        .join(Block, Block.cast_id == Cast.id)
        .where(Block.product_id == product_id, Cast.user_id == user.id)
        .distinct()
    )
    casts_via_cp = await db.execute(
        select(Cast.id, Cast.name, Cast.status)
        .join(CastProduct, CastProduct.cast_id == Cast.id)
        .where(CastProduct.product_id == product_id, Cast.user_id == user.id)
        .distinct()
    )
    seen_ids = set()
    casts_list = []
    for row in list(casts_via_blocks.all()) + list(casts_via_cp.all()):
        if row.id not in seen_ids:
            seen_ids.add(row.id)
            casts_list.append({
                "id": row.id,
                "name": row.name or "Untitled Cast",
                "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            })
    resp["casts"] = casts_list

    return resp


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    req: ProductUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    update_data = req.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)

    await db.commit()
    await db.refresh(product)
    try:
        safe_changes = {k: (v[:200] if isinstance(v, str) else v) for k, v in update_data.items()}
        await audit_log.record(
            db, user_id=user.id, action="product.update", entity_type="product",
            entity_id=product_id, after=safe_changes,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return await _product_to_response(product, db)


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    # Block deletion if product is used in an active/generating cast
    active_statuses = [CastStatus.GENERATING, CastStatus.LIVE, CastStatus.SCHEDULED]
    active_cast = await db.execute(
        select(Cast.id, Cast.name)
        .join(Block, Block.cast_id == Cast.id)
        .where(
            Block.product_id == product_id,
            Cast.user_id == user.id,
            Cast.status.in_(active_statuses),
        )
        .limit(1)
    )
    row = active_cast.first()
    if not row:
        active_cast_cp = await db.execute(
            select(Cast.id, Cast.name)
            .join(CastProduct, CastProduct.cast_id == Cast.id)
            .where(
                CastProduct.product_id == product_id,
                Cast.user_id == user.id,
                Cast.status.in_(active_statuses),
            )
            .limit(1)
        )
        row = active_cast_cp.first()
    if row:
        raise HTTPException(
            409,
            f"Cannot delete: product is used in active cast \"{row.name or 'Untitled'}\"",
        )

    try:
        await audit_log.record(
            db, user_id=user.id, action="product.delete", entity_type="product",
            entity_id=product_id, before={"name": product.name},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
    await db.delete(product)
    await db.commit()


# ── Asset Management ──

@router.post("/{product_id}/assets/upload")
async def upload_asset(
    product_id: str,
    file: UploadFile = File(...),
    asset_type: str = Query("product_shot"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    content = await file.read()
    asset_id = f"pa_{uuid.uuid4().hex[:12]}"

    # Determine media type from content type
    ct = file.content_type or ""
    if "video" in ct:
        media_type = "video"
        ext = "mp4"
    elif "image" in ct:
        media_type = "image"
        ext = "jpg" if "jpeg" in ct else "png"
    else:
        media_type = "image"
        ext = "bin"

    r2_key = f"products/{product_id}/assets/{asset_id}.{ext}"
    await r2.upload_bytes(content, r2_key, ct or f"{media_type}/{ext}")

    asset = ProductAsset(
        id=asset_id,
        product_id=product_id,
        user_id=user.id,
        asset_type=asset_type,
        media_type=media_type,
        r2_key=r2_key,
        r2_url=r2.get_public_url(r2_key),
        file_size_bytes=len(content),
    )
    db.add(asset)

    # asset_type="cover" is the frontend's signal that this upload IS the
    # product's cover, not just another gallery shot — every cover_image_url
    # in every response is derived solely from product.cover_image_key, so
    # without this the upload succeeded but the cover never visibly changed.
    if asset_type == "cover" and media_type == "image":
        product.cover_image_key = r2_key

    await db.commit()
    await db.refresh(asset)

    try:
        await audit_log.record(
            db, user_id=user.id, action="product.asset_upload", entity_type="product",
            entity_id=product_id, after={"asset_id": asset.id, "asset_type": asset.asset_type, "media_type": asset.media_type},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "media_type": asset.media_type,
        "r2_key": asset.r2_key,
        "r2_url": asset.r2_url,
        "file_size_bytes": asset.file_size_bytes,
    }


@router.post("/{product_id}/assets/record")
async def record_asset(
    product_id: str,
    file: UploadFile = File(...),
    asset_type: str = Query("demo"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a recorded video/audio blob as an asset."""
    return await upload_asset(product_id, file, asset_type, user, db)


@router.get("/{product_id}/assets")
async def list_product_assets(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all assets for a product, ordered for gallery / carousel display.

    Ordering: position ascending (cover at 0), then created_at ascending so
    legacy rows without explicit position appear in import order.
    """
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    result = await db.execute(
        select(ProductAsset)
        .where(ProductAsset.product_id == product_id)
        .order_by(ProductAsset.position.asc(), ProductAsset.created_at.asc())
    )
    assets = result.scalars().all()
    return [
        {
            "id": a.id,
            "asset_type": a.asset_type,
            "media_type": a.media_type,
            "r2_key": a.r2_key,
            "r2_url": a.r2_url or "",
            "position": a.position or 0,
            "duration_seconds": a.duration_seconds or 0,
            "width": a.width or 0,
            "height": a.height or 0,
            "file_size_bytes": a.file_size_bytes or 0,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in assets
    ]


@router.delete("/{product_id}/assets/{asset_id}", status_code=204)
async def delete_asset(
    product_id: str,
    asset_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(ProductAsset, asset_id)
    if not asset or asset.product_id != product_id:
        raise HTTPException(404, "Asset not found")
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")
    try:
        await audit_log.record(
            db, user_id=user.id, action="product.asset_delete", entity_type="product",
            entity_id=product_id, before={"asset_id": asset_id},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
    await db.delete(asset)
    await db.commit()


# ── TikTok Shop Import ──

class TikTokShopSearchRequest(BaseModel):
    query: str
    max_products: int = 50


# ── DISABLED: bulk Apify search endpoints (cost $241/month) ────────────
# Use POST /api/products/from-url to import a single product from a
# URL. Bulk search / backfill via TikTok-shop name-search are gone.
_BULK_SEARCH_DISABLED = HTTPException(
    410,
    "Bulk TikTok Shop search is disabled. Use POST /api/products/from-url to import a product by URL.",
)


@router.post("/import-tiktok")
async def import_tiktok_products(
    req: TikTokShopSearchRequest,
    user: User = Depends(get_current_user),
):
    """DISABLED — was: search TikTok Shop via Apify, preview results."""
    raise _BULK_SEARCH_DISABLED


@router.post("/search-tiktok-shop")
async def search_tiktok_shop(
    req: TikTokShopSearchRequest,
    user: User = Depends(get_current_user),
):
    """DISABLED — was: TikTok Shop name search."""
    raise _BULK_SEARCH_DISABLED


@router.post("/backfill-covers")
async def backfill_covers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """DISABLED — was: backfill cover images by Apify-searching TikTok
    Shop for each product missing a cover. Each search was an Apify
    actor run (~$0.10 each) which added up fast on bulk operations.

    To re-import a single product with proper covers, paste its source
    URL into POST /api/products/from-url, or use the per-product
    POST /api/products/{id}/refresh endpoint which re-runs the URL
    resolver against the existing source URL (no name-based search).
    """
    raise _BULK_SEARCH_DISABLED


@router.post("/proxy-product-image")
async def proxy_product_image(
    image_url: str = Body(..., embed=True),
    user: User = Depends(get_current_user),
):
    """Download a product image and re-upload to R2."""
    import httpx as _httpx
    from services.r2_storage import get_r2_storage_service

    r2 = get_r2_storage_service()
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
    r2_key = f"products/{url_hash}/image.jpg"

    try:
        async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(image_url)
            if resp.status_code != 200 or len(resp.content) < 500:
                raise HTTPException(400, "Could not download image")
            await r2.upload_bytes(resp.content, r2_key, "image/jpeg")
            return {"image_url": r2.get_public_url(r2_key), "r2_key": r2_key}
    except _httpx.HTTPError as e:
        raise HTTPException(500, f"Image proxy failed: {str(e)[:200]}")


# ── Overlay Generation ──

@router.post("/{product_id}/generate-overlay")
async def generate_overlay(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate price/discount overlay image."""
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    # Generate simple price tag overlay as SVG → PNG via placeholder
    price_text = f"${product.price:.2f}"
    discount_text = f"{int(product.discount_percent)}% OFF" if product.discount_percent else ""

    asset_id = f"pa_{uuid.uuid4().hex[:12]}"
    overlay_config = {
        "type": "price_tag",
        "price": price_text,
        "discount": discount_text,
        "product_name": product.name,
        "style": "modern",
    }

    # For now create a config-only overlay (frontend renders it)
    r2_key = f"products/{product_id}/overlays/{asset_id}.json"
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    import json
    await r2.upload_bytes(
        json.dumps(overlay_config).encode(),
        r2_key,
        "application/json",
    )

    asset = ProductAsset(
        id=asset_id,
        product_id=product_id,
        user_id=user.id,
        asset_type="price_overlay",
        media_type="overlay",
        r2_key=r2_key,
        r2_url=r2.get_public_url(r2_key),
        overlay_config=overlay_config,
    )
    db.add(asset)
    await db.commit()

    return {
        "id": asset.id,
        "overlay_config": overlay_config,
        "r2_url": asset.r2_url,
    }


# ── AI Image Generation ──

async def _resolve_product_source_image(product: Product, db: AsyncSession, r2) -> str:
    """Return a public R2 URL for the product's real reference photo.

    Prefers the stored cover; for TikTok-sourced products with no cover yet,
    materializes the TikTok CDN cover to R2 first. Returns "" when no
    reference photo exists — callers that need one (image-to-image /
    image-to-video generation) should treat that as a 400.
    """
    if product.cover_image_key:
        return r2.get_public_url(product.cover_image_key)

    if product.tiktok_product_id:
        from models.trending_product import TrendingProduct
        tp = await db.scalar(
            select(TrendingProduct).where(
                TrendingProduct.tiktok_product_id == product.tiktok_product_id,
                TrendingProduct.cover_image_url != "",
            )
        )
        if tp and tp.cover_image_url:
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(tp.cover_image_url, headers={"Referer": ""})
                    if resp.status_code == 200 and len(resp.content) > 500:
                        r2_key = f"products/covers/{product.tiktok_product_id}.jpg"
                        await r2.upload_bytes(resp.content, r2_key, "image/jpeg")
                        product.cover_image_key = r2_key
                        await db.commit()
                        return r2.get_public_url(r2_key)
            except Exception:
                pass

    return ""


@router.post("/{product_id}/generate-ai-images")
async def generate_ai_images(
    product_id: str,
    style: str = Body("product_shot"),
    custom_prompt: str = Body(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate styled product photos via FLUX Kontext (image-to-image).

    Kontext edits the product's *real* cover photo instead of hallucinating
    a fresh product from text alone — FLUX schnell (pure text-to-image) was
    generating a different-looking product (wrong design/color) every time
    because it had no reference to what the product actually looks like.
    """
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise HTTPException(503, "AI generation unavailable: FAL_API_KEY not configured")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    source_image_url = await _resolve_product_source_image(product, db, r2)
    if not source_image_url:
        raise HTTPException(400, "Product needs a cover image. Upload one or pick a different product.")

    scene_prompts = {
        "product_shot": "Professional studio product photography on a clean white background with soft studio lighting, 4K quality.",
        "lifestyle": f"{product.name} shown in a stylish lifestyle setting with natural lighting, editorial photography style.",
        "swatch": f"Extreme close-up macro photography of {product.name}, showing fine texture and material detail.",
    }
    # Scene/style instruction is stated first (FLUX Kontext weighs earlier
    # instructions more heavily), then the identity-preservation clause —
    # same ordering as services/flux_kontext.py's avatar-frame edits.
    scene_instruction = (
        custom_prompt.strip() if custom_prompt and custom_prompt.strip()
        else scene_prompts.get(style, scene_prompts["product_shot"])
    )
    preserve_clause = (
        "Keep the exact same product from the reference photo completely unchanged — "
        "identical shape, color, materials, design, and any logos or text. "
        "Do not restyle, redesign, or recolor the product."
    )
    prompt = f"{scene_instruction} {preserve_clause}"

    try:
        import asyncio
        import fal_client

        def _run_flux_kontext():
            os.environ["FAL_KEY"] = app_settings.FAL_API_KEY
            return fal_client.subscribe(
                "fal-ai/flux-pro/kontext",
                arguments={
                    "prompt": prompt,
                    "image_url": source_image_url,
                    "guidance_scale": 3.5,
                    "num_inference_steps": 28,
                    "output_format": "jpeg",
                },
            )

        result = await asyncio.to_thread(_run_flux_kontext)
        image_url = result["images"][0]["url"]

        # Download and upload to R2
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            asset_id = f"pa_{uuid.uuid4().hex[:12]}"
            r2_key = f"products/{product_id}/assets/{asset_id}.jpg"
            await r2.upload_bytes(resp.content, r2_key, "image/jpeg")

            asset = ProductAsset(
                id=asset_id, product_id=product_id, user_id=user.id,
                asset_type="ai_generated_image", media_type="image",
                r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
                file_size_bytes=len(resp.content),
                generation_prompt=prompt, generation_model="flux_pro_kontext",
            )
            db.add(asset)
            await db.commit()

        return {"id": asset.id, "r2_url": asset.r2_url, "prompt": prompt}

    except Exception as e:
        raise HTTPException(500, f"AI image generation failed: {str(e)[:200]}")


# ── AI Video Generation ──

_KLING_MODELS = {
    "pro": "fal-ai/kling-video/v1.5/pro/image-to-video",
    "fast": "fal-ai/kling-video/v1.6/standard/image-to-video",
}

# v1.5/pro's input schema has aspect_ratio (16:9 / 9:16 / 1:1); v1.6/standard's
# does not — only prompt/image_url/duration/negative_prompt/cfg_scale. Sending
# it there is silently swallowed by the queue accept but breaks the run before
# a result ever exists (POST 200, then the result fetch 404s).
_KLING_SUPPORTS_ASPECT_RATIO = {"pro"}

# cfg_scale (0-1, fal default 0.5) controls how strongly the output follows
# the text prompt vs. staying close to the source image. v1.6/standard was
# observed producing near-static output (product visible, no motion) at the
# default — pushed higher here so it follows the motion instruction more
# assertively. v1.5/pro already produces correct motion at the default, so
# it's left alone.
_KLING_CFG_SCALE = {"fast": 0.8}


@router.post("/{product_id}/generate-ai-video")
async def generate_ai_video(
    product_id: str,
    style: str = Body("product_showcase"),
    duration_seconds: int = Body(5),
    custom_prompt: str = Body(""),
    quality: str = Body("pro"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a short product video using fal.ai image-to-video."""
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    prompts = {
        "product_showcase": f"Slow cinematic rotation of {product.name}, studio lighting, white background, smooth camera movement, product photography style, 4K quality",
        "lifestyle": f"{product.name} being used naturally, soft natural lighting, lifestyle photography, warm tones",
        # Kling's image-to-video locks frame 1 to the product's actual cover
        # photo — there is no box in that photo. Any prompt asking the model
        # to materialize a box, cover the product, then remove it forces it
        # to hallucinate an object that was never in frame, which it does
        # badly (a half-formed cover-and-remove, or a backwards
        # shoe-then-box-then-no-box sequence). Framing the "reveal" as
        # camera/lighting motion instead of object insertion plays to what
        # these models can actually do reliably.
        "unboxing": f"{product.name} sits on display exactly as shown. Camera starts close and slightly above, then pulls back and rises in a smooth motion as studio lighting brightens, revealing the full product in a satisfying unveiling shot, clean background",
        "comparison": f"Before and after using {product.name}, split screen effect, dramatic transformation",
    }
    prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else prompts.get(style, prompts["product_showcase"])

    from config import settings as app_settings
    if not app_settings.FAL_API_KEY:
        raise HTTPException(503, "AI generation unavailable: FAL_API_KEY not configured")

    # Need a source image — try R2 cover, fall back to TikTok CDN
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    source_image_url = await _resolve_product_source_image(product, db, r2)
    if not source_image_url:
        raise HTTPException(400, "Product needs a cover image. Upload one or pick a different product.")

    kling_tier = quality if quality in _KLING_MODELS else "pro"
    kling_model = _KLING_MODELS[kling_tier]
    # Derived from the model id itself (not hand-typed) so the stored label
    # can't drift out of sync with _KLING_MODELS on a future version bump.
    _model_parts = kling_model.split("/")
    kling_model_label = f"kling_{_model_parts[2]}_{_model_parts[3]}"

    try:
        import asyncio
        import fal_client

        def _run_kling():
            os.environ["FAL_KEY"] = app_settings.FAL_API_KEY
            kling_args = {
                "prompt": prompt,
                "image_url": source_image_url,
                "duration": str(duration_seconds),
            }
            if kling_tier in _KLING_SUPPORTS_ASPECT_RATIO:
                kling_args["aspect_ratio"] = "9:16"
            if kling_tier in _KLING_CFG_SCALE:
                kling_args["cfg_scale"] = _KLING_CFG_SCALE[kling_tier]
            return fal_client.subscribe(kling_model, arguments=kling_args)

        result = await asyncio.to_thread(_run_kling)
        video_url = result["video"]["url"]

        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(video_url)
            asset_id = f"pa_{uuid.uuid4().hex[:12]}"
            r2_key = f"products/{product_id}/assets/{asset_id}.mp4"
            await r2.upload_bytes(resp.content, r2_key, "video/mp4")

            asset = ProductAsset(
                id=asset_id, product_id=product_id, user_id=user.id,
                asset_type="ai_generated_video", media_type="video",
                r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
                duration_seconds=duration_seconds,
                file_size_bytes=len(resp.content),
                generation_prompt=prompt, generation_model=kling_model_label,
            )
            db.add(asset)
            await db.commit()

        return {"id": asset.id, "r2_url": asset.r2_url, "prompt": prompt, "duration": duration_seconds}

    except Exception as e:
        raise HTTPException(500, f"AI video generation failed: {str(e)[:200]}")


# ── Background Removal ──

@router.post("/{product_id}/remove-background")
async def remove_product_background(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove background from product cover image using rembg.
    Creates a new asset with transparent background (PNG).
    """
    product = await db.get(Product, product_id)
    if not product or product.user_id != user.id:
        raise HTTPException(404, "Product not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    # Materialize TikTok cover to R2 if needed
    if not product.cover_image_key and product.tiktok_product_id:
        from models.trending_product import TrendingProduct
        tp = await db.scalar(
            select(TrendingProduct).where(
                TrendingProduct.tiktok_product_id == product.tiktok_product_id,
                TrendingProduct.cover_image_url != "",
            )
        )
        if tp and tp.cover_image_url:
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(tp.cover_image_url, headers={"Referer": ""})
                    if resp.status_code == 200 and len(resp.content) > 500:
                        r2_key = f"products/covers/{product.tiktok_product_id}.jpg"
                        await r2.upload_bytes(resp.content, r2_key, "image/jpeg")
                        product.cover_image_key = r2_key
                        await db.commit()
            except Exception:
                pass

    if not product.cover_image_key:
        raise HTTPException(400, "Product has no cover image")

    # Download cover image
    import httpx
    cover_url = r2.get_public_url(product.cover_image_key)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(cover_url)
        if resp.status_code != 200:
            raise HTTPException(500, "Failed to download cover image")
        image_bytes = resp.content

    # Remove background (CPU-intensive, offload to thread to avoid blocking event loop)
    try:
        from rembg import remove as rembg_remove
        import asyncio
        result_bytes = await asyncio.to_thread(rembg_remove, image_bytes)
    except ImportError:
        raise HTTPException(500, "rembg not installed on server")
    except Exception as e:
        raise HTTPException(500, f"Background removal failed: {str(e)[:200]}")

    # Upload as PNG (with transparency)
    asset_id = f"pa_{uuid.uuid4().hex[:12]}"
    r2_key = f"products/{product_id}/assets/{asset_id}_nobg.png"
    await r2.upload_bytes(result_bytes, r2_key, "image/png")

    asset = ProductAsset(
        id=asset_id, product_id=product_id, user_id=user.id,
        asset_type="background_removed", media_type="image",
        r2_key=r2_key, r2_url=r2.get_public_url(r2_key),
        file_size_bytes=len(result_bytes),
    )
    db.add(asset)
    await db.commit()

    return {"id": asset.id, "r2_url": asset.r2_url, "type": "background_removed"}


# ── AI-generated sample reviews (Feature 4 — spec lines 466-516) ──
# Generates 25 realistic sample reviews on user click using the cheap LLM.
# Reviews are samples for UX, not real reviews — never persisted.

class _ReviewItem(BaseModel):
    id: str
    stars: int
    author: str
    text: str
    verified: bool
    date: str


class GenerateReviewsResponse(BaseModel):
    reviews: List[_ReviewItem]
    generated_at: str
    model_used: str


# Per (user_id, product_id) -> last unix timestamp the endpoint was called.
# In-memory rate limiter; resets on process restart, which is fine for a
# cost-control guard rail (the LLM call is the expensive part).
_REVIEW_GEN_RATE_LIMIT_SECONDS = 60
_review_gen_last_called: dict[tuple[str, str], float] = {}

_REVIEW_MODEL = "anthropic/claude-3-haiku"


def _strip_review_json_fences(raw: str) -> str:
    """Strip markdown fences and surrounding chatter from an LLM JSON reply."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
        cleaned = cleaned.strip()
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
    return cleaned


def _build_reviews_prompt(product: Product) -> str:
    desc = (product.description or "")[:300]
    return f"""Generate 25 realistic customer reviews for this product:

Product: {product.name}
Description: {desc}
Price: ${product.price}
Rating: {product.rating or 0}/5 ({product.review_count or 0} reviews)

Generate EXACTLY 5 reviews for each star rating (5 stars, 4 stars, 3 stars, 2 stars, 1 star).
Distribution should feel natural:
- 5-star reviews: enthusiastic, mention specific benefits
- 4-star reviews: positive but mention one minor issue
- 3-star reviews: balanced, "it's okay but..."
- 2-star reviews: disappointed, expected more
- 1-star reviews: negative but realistic (not abusive)

Each review needs:
- stars: integer 1-5
- author: first name + last initial, max 50 chars (e.g. "Sarah M.")
- text: 30-60 words, between 20 and 200 characters
- verified: boolean, ~80% true

Return JSON array only, no markdown:
[{{"stars": 5, "author": "Sarah M.", "text": "...", "verified": true}}, ...]"""


def _validate_reviews(parsed) -> list[dict]:
    """Validate raw review dicts; drop any that don't fit the contract."""
    if not isinstance(parsed, list):
        return []
    valid: list[dict] = []
    for r in parsed:
        if not isinstance(r, dict):
            continue
        try:
            stars = int(r.get("stars"))
        except (TypeError, ValueError):
            continue
        if stars < 1 or stars > 5:
            continue
        author = r.get("author")
        if not isinstance(author, str) or not author.strip():
            continue
        author = author.strip()[:50]
        text = r.get("text")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if len(text) < 20:
            continue
        if len(text) > 200:
            text = text[:200].rstrip()
        verified = r.get("verified", True)
        if not isinstance(verified, bool):
            verified = bool(verified)
        valid.append({
            "stars": stars,
            "author": author,
            "text": text,
            "verified": verified,
        })
    return valid


def _distribution_acceptable(reviews: list[dict]) -> bool:
    """5 per star ±1 — accept 4-6 per bucket."""
    counts = {s: 0 for s in range(1, 6)}
    for r in reviews:
        counts[r["stars"]] += 1
    return all(4 <= counts[s] <= 6 for s in range(1, 6))


async def _call_llm_for_reviews(prompt: str, system_prompt: str) -> str:
    from services.openrouter import get_openrouter_service
    return await get_openrouter_service().generate_text(
        prompt=prompt,
        system_prompt=system_prompt,
        model=_REVIEW_MODEL,
        max_tokens=2048,
        temperature=0.9,
    )


@router.post("/{product_id}/generate-reviews", response_model=GenerateReviewsResponse)
async def generate_reviews(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate 25 sample reviews (5 per star bucket) via the cheap LLM.

    Reviews are ephemeral samples — not persisted. Rate-limited per (user,
    product) to one call per 60s as a cost guard.
    """
    import json
    import random
    import time
    from datetime import datetime, timedelta, timezone

    try:
        product = await db.get(Product, product_id)
        if not product or product.user_id != user.id:
            raise HTTPException(404, "Product not found")

        rate_key = (user.id, product_id)
        now_ts = time.monotonic()
        last = _review_gen_last_called.get(rate_key)
        if last is not None and (now_ts - last) < _REVIEW_GEN_RATE_LIMIT_SECONDS:
            wait = int(_REVIEW_GEN_RATE_LIMIT_SECONDS - (now_ts - last))
            raise HTTPException(
                429,
                f"Sample reviews were just generated for this product. Try again in {wait}s.",
            )
        _review_gen_last_called[rate_key] = now_ts

        prompt = _build_reviews_prompt(product)
        system_prompt = "You write authentic product reviews. Return valid JSON only — no markdown, no explanation."

        # First call
        raw = await _call_llm_for_reviews(prompt, system_prompt)

        parsed = None
        try:
            parsed = json.loads(_strip_review_json_fences(raw))
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

        # JSON retry if parse failed
        if parsed is None:
            try:
                retry_prompt = (
                    "Your previous response was not valid JSON. Return ONLY a "
                    "valid JSON array of 25 review objects, no markdown, no "
                    "explanation.\n\n" + prompt
                )
                raw = await _call_llm_for_reviews(retry_prompt, system_prompt)
                parsed = json.loads(_strip_review_json_fences(raw))
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                raise HTTPException(
                    500,
                    "Couldn't generate reviews right now, please try again.",
                )

        reviews = _validate_reviews(parsed)

        # Regenerate once if too few survived validation or distribution skewed
        if len(reviews) < 15 or not _distribution_acceptable(reviews):
            try:
                raw = await _call_llm_for_reviews(prompt, system_prompt)
                parsed_retry = json.loads(_strip_review_json_fences(raw))
                retry_reviews = _validate_reviews(parsed_retry)
                if len(retry_reviews) > len(reviews):
                    reviews = retry_reviews
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                # Fall through with whatever we have

        if not reviews:
            raise HTTPException(
                500,
                "Couldn't generate reviews right now, please try again.",
            )

        # Add fake dates within last 6 months and ids
        now_dt = datetime.now(timezone.utc)
        for r in reviews:
            days_ago = random.randint(1, 180)
            r["date"] = (now_dt - timedelta(days=days_ago)).strftime("%b %d, %Y")
            r["id"] = f"rev_{uuid.uuid4().hex[:8]}"

        # 5-star first, then 4, etc.
        reviews.sort(key=lambda r: -r["stars"])

        # Cost tracking — best-effort, never fails the request
        try:
            from services.usage_logger import log_api_usage
            await log_api_usage(
                user_id=user.id,
                service="openrouter",
                operation="review_generation",
                success=True,
                cost_cents=0,  # ~$0.002 per call; refined once usage_tracker (PR #18) lands
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

        return {
            "reviews": reviews,
            "generated_at": now_dt.isoformat(),
            "model_used": _REVIEW_MODEL,
        }
    except HTTPException:
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            500,
            "Couldn't generate reviews right now, please try again.",
        )
