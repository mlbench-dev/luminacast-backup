"""Product crud endpoints — split from the former routers/products.py."""

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
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
import sentry_sdk
from database import get_db
from models.user import User, TeamRole
from models.product import Product
from models.product_asset import ProductAsset
from models.cast import Cast, CastProduct
from models.block import Block
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

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

@router.post("", status_code=201)
async def create_product(
    req: ProductCreate,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        user_id=ctx.workspace_owner_id,        name=req.name,
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

async def _materialize_product_assets(
    *,
    db: AsyncSession,
    user: User,
    workspace_owner_id: str,
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
                user_id=workspace_owner_id,
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
            user_id=workspace_owner_id,
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

@router.post("/from-url", status_code=201)
async def import_from_url(
    req: FromUrlRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Import a product from any URL (TikTok, Amazon, or generic e-commerce).

    De-duplicates by source_product_id (TikTok) or product_url (others).
    Returns existing product if already imported.
    """
    from services.url_product_resolver import (
        resolve_product_url,
        TikTokBlockedError,
        GenericSiteBlockedError,
        AmazonBlockedError,
    )

    try:
        resolved = await resolve_product_url(req.url)
    except AmazonBlockedError as exc:
        sentry_sdk.capture_exception(exc)
        logger.info(
            "[from-url-blocked] amazon manual-entry fallback url=%s reason=%s",
            exc.source_url, exc.reason,
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "needs_manual_entry",
                "source": "amazon",
                "source_product_id": None,
                "source_url": exc.source_url,
                "message": exc.reason,
            },
        )
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
        # Unknown / unclassified failure. Keep the raw detail in logs + Sentry;
        # give the user a plain, actionable message instead of an exception dump.
        sentry_sdk.capture_exception(exc)
        logger.error("[from-url] unresolved url=%s error=%s", req.url, str(exc)[:500])
        raise HTTPException(
            400,
            "We couldn't import this product automatically. The link may be "
            "unsupported, or the site is blocking us right now. Try a different "
            "link, or add the product details manually.",
        )

    # De-duplicate: check if user already has this product
    existing = None
    if resolved.source == "tiktok" and resolved.source_product_id:
        existing = await db.scalar(
            select(Product).where(
                Product.user_id == ctx.workspace_owner_id,
                Product.tiktok_product_id == resolved.source_product_id,
            )
        )
    elif resolved.source_url:
        existing = await db.scalar(
            select(Product).where(
                Product.user_id == ctx.workspace_owner_id,
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
        user_id=ctx.workspace_owner_id,        name=resolved.title or "Imported Product",
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
            workspace_owner_id=ctx.workspace_owner_id,
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
            user_id=ctx.workspace_owner_id,            event_type="product_import",
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

@router.post("/{product_id}/refresh")
async def refresh_product(
    product_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        select(Product).where(Product.id == product_id, Product.user_id == ctx.workspace_owner_id)
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    query = select(Product).where(
        Product.user_id == ctx.workspace_owner_id,
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
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
        .where(Block.product_id == product_id, Cast.user_id == ctx.workspace_owner_id)
        .distinct()
    )
    casts_via_cp = await db.execute(
        select(Cast.id, Cast.name, Cast.status)
        .join(CastProduct, CastProduct.cast_id == Cast.id)
        .where(CastProduct.product_id == product_id, Cast.user_id == ctx.workspace_owner_id)
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or product.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Product not found")

    # Block deletion if any cast references this product — regardless of
    # status. The DB itself refuses this delete for as long as ANY cast
    # (any status, forever) references the product (Block.product_id /
    # CastProduct.product_id have no ondelete rule), so checking only a
    # hand-picked subset of "active" statuses is exactly what let this
    # guard go stale before: it missed GENERATING_TTS and GENERATING_VIDEOS
    # entirely, letting a delete attempt during those phases fall through
    # to an unhandled DB error instead of this clean message. Match what
    # the DB actually enforces instead of guessing.
    block_cast = await db.execute(
        select(Cast.id, Cast.name)
        .join(Block, Block.cast_id == Cast.id)
        .where(
            Block.product_id == product_id,
            Cast.user_id == ctx.workspace_owner_id,
        )
        .limit(1)
    )
    row = block_cast.first()
    if not row:
        cp_cast = await db.execute(
            select(Cast.id, Cast.name)
            .join(CastProduct, CastProduct.cast_id == Cast.id)
            .where(
                CastProduct.product_id == product_id,
                Cast.user_id == ctx.workspace_owner_id,
            )
            .limit(1)
        )
        row = cp_cast.first()
    if row:
        raise HTTPException(
            409,
            f"Cannot delete: product is used in cast \"{row.name or 'Untitled'}\". Remove it from that cast first.",
        )

    try:
        await audit_log.record(
            db, user_id=user.id, action="product.delete", entity_type="product",
            entity_id=product_id, before={"name": product.name},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
    await db.delete(product)
    try:
        await db.commit()
    except IntegrityError as e:
        # Safety net in case the check above missed a referencing row —
        # never let a raw DB constraint error surface as an unhandled 500.
        await db.rollback()
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            409,
            "This product is still referenced by other data and can't be deleted.",
        )

_MAX_RAW = os.environ.get("MAX_PRODUCT_ASSETS_PER_PRODUCT", "0")

try:
    MAX_PRODUCT_ASSETS_PER_PRODUCT = int(_MAX_RAW)
except ValueError:
    MAX_PRODUCT_ASSETS_PER_PRODUCT = 0  # treat malformed env as no cap
