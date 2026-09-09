"""Proxy Pexels API to avoid exposing API key to the frontend."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.user import User
from models.user_video import UserVideoAsset
from routers.auth import get_current_user
from services.pexels import get_pexels_client
from services.r2_storage import get_r2_storage_service
from services.stock_query import stockify_query
from services.video_thumbnail import extract_video_thumbnail_jpeg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-media", tags=["stock-media"])


def _log(level: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps({
            "service": "stock-media",
            "level": level,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }),
    )


@router.get("/photos")
async def search_stock_photos(
    q: str = Query(..., min_length=1, max_length=100),
    orientation: Optional[str] = Query(None, pattern="^(landscape|portrait|square)$"),
    size: Optional[str] = Query(None, pattern="^(small|medium|large)$"),
    color: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=40),
    user: User = Depends(get_current_user),
):
    # Strip shot-style / aesthetic / filler words so a phrase like
    # "hoodie flat lay charcoal" searches for "charcoal hoodie" instead of
    # degrading to unrelated flat-lay footage.
    search_q = stockify_query(q) or q
    try:
        client = get_pexels_client()
        result = await client.search_photos(search_q, orientation, size, color, page, per_page)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except httpx.HTTPStatusError as e:
        _log("error", f"Pexels photos API error: {e}", status_code=e.response.status_code)
        raise HTTPException(502, "Stock media service unavailable")
    except Exception as e:
        _log("error", f"Pexels photos search failed: {e}")
        raise HTTPException(502, "Stock media service unavailable")

    photos = []
    for p in result.get("photos", []):
        photos.append({
            "id": p["id"],
            "type": "photo",
            "src": p["src"]["large2x"],
            "thumb": p["src"]["medium"],
            "width": p["width"],
            "height": p["height"],
            "photographer": p["photographer"],
            "photographer_url": p["photographer_url"],
            "pexels_url": p["url"],
            "avg_color": p.get("avg_color"),
            "alt": p.get("alt", ""),
        })
    return {
        "results": photos,
        "total_results": result.get("total_results", 0),
        "page": page,
        "per_page": per_page,
        "query": q,
        "resolved_query": search_q,
    }


@router.get("/videos")
async def search_stock_videos(
    q: str = Query(..., min_length=1, max_length=100),
    orientation: Optional[str] = Query(None, pattern="^(landscape|portrait|square)$"),
    min_duration: Optional[int] = Query(None, ge=1, le=300),
    max_duration: Optional[int] = Query(None, ge=1, le=300),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=1, le=40),
    user: User = Depends(get_current_user),
):
    # See search_stock_photos — collapse a shot-style phrase to its subject.
    search_q = stockify_query(q) or q
    try:
        client = get_pexels_client()
        result = await client.search_videos(search_q, orientation, None, min_duration, max_duration, page, per_page)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except httpx.HTTPStatusError as e:
        _log("error", f"Pexels videos API error: {e}", status_code=e.response.status_code)
        raise HTTPException(502, "Stock media service unavailable")
    except Exception as e:
        _log("error", f"Pexels videos search failed: {e}")
        raise HTTPException(502, "Stock media service unavailable")

    videos = []
    for v in result.get("videos", []):
        best_file = None
        for vf in v.get("video_files", []):
            if vf.get("quality") == "hd" and vf.get("file_type") == "video/mp4":
                best_file = vf
                break
        if not best_file and v.get("video_files"):
            best_file = v["video_files"][0]

        videos.append({
            "id": v["id"],
            "type": "video",
            "src": best_file["link"] if best_file else None,
            "thumb": v.get("image", v.get("video_pictures", [{}])[0].get("picture", "")),
            "width": v["width"],
            "height": v["height"],
            "duration": v["duration"],
            "photographer": v.get("user", {}).get("name", ""),
            "photographer_url": v.get("user", {}).get("url", ""),
            "pexels_url": v["url"],
        })
    return {
        "results": videos,
        "total_results": result.get("total_results", 0),
        "page": page,
        "per_page": per_page,
        "query": q,
        "resolved_query": search_q,
    }


class ImportStockMediaRequest(BaseModel):
    url: str
    type: str  # "photo" or "video"
    pexels_id: int
    name: Optional[str] = None
    thumb_url: Optional[str] = None  # Pexels-provided poster image (video only)


@router.post("/import")
async def import_stock_media(
    payload: ImportStockMediaRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a Pexels asset and store in R2 as a user video/image asset."""
    _log("info", f"Importing stock {payload.type} pexels_id={payload.pexels_id}", user_id=user.id)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(payload.url)
            resp.raise_for_status()
            content = resp.content
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log("error", f"Failed to download stock media: {e}", url=payload.url)
        raise HTTPException(502, f"Failed to download media from Pexels: {str(e)[:200]}")

    ext = "mp4" if payload.type == "video" else "jpg"
    content_type = "video/mp4" if payload.type == "video" else "image/jpeg"
    asset_id = f"uv_{uuid.uuid4().hex[:16]}"
    r2_key = f"user-videos/{user.id}/stock-imports/{asset_id}.{ext}"

    r2 = get_r2_storage_service()
    await r2.upload_bytes(content, r2_key, content_type=content_type)

    # For video imports, store a thumbnail too. Prefer the Pexels-provided
    # poster URL when available (no ffmpeg needed); otherwise extract one.
    thumbnail_r2_key: Optional[str] = None
    if payload.type == "video":
        thumb_bytes: bytes = b""
        if payload.thumb_url:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    tresp = await client.get(payload.thumb_url)
                    tresp.raise_for_status()
                    thumb_bytes = tresp.content
            except Exception as e:
                sentry_sdk.capture_exception(e)
                _log("warn", f"Failed to fetch Pexels thumbnail: {e}", url=payload.thumb_url)
                thumb_bytes = b""
        if not thumb_bytes:
            try:
                thumb_bytes = await extract_video_thumbnail_jpeg(content, suffix=".mp4")
            except Exception as e:
                sentry_sdk.capture_exception(e)
                _log("warn", f"Stock video thumbnail extraction failed: {e}")
                thumb_bytes = b""
        if thumb_bytes:
            thumb_key = f"user-videos/{user.id}/stock-imports/{asset_id}_thumb.jpg"
            try:
                await r2.upload_bytes(thumb_bytes, thumb_key, content_type="image/jpeg")
                thumbnail_r2_key = thumb_key
            except Exception as e:
                sentry_sdk.capture_exception(e)
                _log("warn", f"Stock video thumbnail upload failed: {e}")

    asset = UserVideoAsset(
        id=asset_id,
        user_id=user.id,
        name=payload.name or f"Pexels {payload.type} {payload.pexels_id}",
        r2_key=r2_key,
        thumbnail_r2_key=thumbnail_r2_key,
        file_size_bytes=len(content),
        original_filename=f"pexels_{payload.pexels_id}.{ext}",
    )
    db.add(asset)
    await db.commit()

    return {
        "asset_id": asset_id,
        "r2_key": r2_key,
        "url": r2.get_public_url(r2_key),
        "thumbnail": r2.get_public_url(thumbnail_r2_key) if thumbnail_r2_key else None,
        "type": payload.type,
    }
