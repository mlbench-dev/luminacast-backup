"""Product tiktok endpoints — split from the former routers/products.py."""

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
from models.user import User, TeamRole
from models.product import Product
from models.product_asset import ProductAsset
from models.cast import Cast, CastStatus, CastProduct
from models.block import Block
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

class TikTokShopSearchRequest(BaseModel):
    query: str
    max_products: int = 50

_BULK_SEARCH_DISABLED = HTTPException(
    410,
    "Bulk TikTok Shop search is disabled. Use POST /api/products/from-url to import a product by URL.",
)

@router.post("/import-tiktok")
async def import_tiktok_products(
    req: TikTokShopSearchRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """DISABLED — was: search TikTok Shop via Apify, preview results."""
    raise _BULK_SEARCH_DISABLED

@router.post("/search-tiktok-shop")
async def search_tiktok_shop(
    req: TikTokShopSearchRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """DISABLED — was: TikTok Shop name search."""
    raise _BULK_SEARCH_DISABLED

@router.post("/backfill-covers")
async def backfill_covers(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
