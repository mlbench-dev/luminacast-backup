"""Pexels API client for stock photos and videos.

Used by the stock-media proxy router (`/api/stock-media/...`) AND by Smart
Cast's `auto_populate_stock_media` to attach b-roll to outline blocks.

Two return styles:
  * `search_photos` / `search_videos` raise on HTTP errors and return the
    raw Pexels JSON. The stock-media router relies on this.
  * `safe_search_photos` / `safe_search_videos` swallow exceptions and
    return None, used by Smart Cast where Pexels failure is non-fatal.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)

PEXELS_BASE = "https://api.pexels.com"


def _log(level: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps({
            "service": "pexels",
            "level": level,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }),
    )


class PexelsClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError("PEXELS_API_KEY not configured")
        self.api_key = api_key
        self.headers = {"Authorization": api_key}

    # ── Strict (raise on error) ──

    async def search_photos(
        self,
        query: str,
        orientation: Optional[str] = None,
        size: Optional[str] = None,
        color: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        params = {"query": query, "page": page, "per_page": min(per_page, 80)}
        if orientation:
            params["orientation"] = orientation
        if size:
            params["size"] = size
        if color:
            params["color"] = color

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{PEXELS_BASE}/v1/search",
                headers=self.headers,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def search_videos(
        self,
        query: str,
        orientation: Optional[str] = None,
        size: Optional[str] = None,
        min_duration: Optional[int] = None,
        max_duration: Optional[int] = None,
        page: int = 1,
        per_page: int = 15,
    ) -> dict[str, Any]:
        params = {"query": query, "page": page, "per_page": min(per_page, 80)}
        if orientation:
            params["orientation"] = orientation
        if size:
            params["size"] = size
        if min_duration:
            params["min_duration"] = min_duration
        if max_duration:
            params["max_duration"] = max_duration

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{PEXELS_BASE}/videos/search",
                headers=self.headers,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_photo(self, photo_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{PEXELS_BASE}/v1/photos/{photo_id}",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_video(self, video_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{PEXELS_BASE}/videos/videos/{video_id}",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    # ── Smart Cast helpers (best-effort, never raise) ──

    async def safe_search_videos(
        self,
        query: str,
        per_page: int = 3,
        orientation: str = "portrait",
        size: str = "medium",
    ) -> dict[str, Any] | None:
        """Best-effort video search for Smart Cast. Returns None on any error."""
        try:
            return await self.search_videos(
                query, orientation=orientation, size=size, per_page=per_page,
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("Pexels safe_search_videos failed for %r: %s", query, exc)
            return None

    async def safe_search_photos(
        self,
        query: str,
        per_page: int = 3,
        orientation: str = "portrait",
    ) -> dict[str, Any] | None:
        """Best-effort photo search for Smart Cast. Returns None on any error."""
        try:
            return await self.search_photos(
                query, orientation=orientation, per_page=per_page,
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("Pexels safe_search_photos failed for %r: %s", query, exc)
            return None


def get_pexels_client() -> PexelsClient:
    """Strict accessor — raises RuntimeError if no key. Used by the stock-media router."""
    if not settings.PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY not configured")
    return PexelsClient(settings.PEXELS_API_KEY)


def get_pexels_client_optional() -> PexelsClient | None:
    """Returns None when no key is set. Used by Smart Cast (best-effort)."""
    if not settings.PEXELS_API_KEY:
        return None
    return PexelsClient(settings.PEXELS_API_KEY)


def pick_best_video_file(video: dict[str, Any]) -> dict[str, Any] | None:
    """Pick a sensible Pexels video_file variant.

    Prefers ≤ 1920-wide MP4. Falls back to the highest-resolution available.
    Returns None when the structure is unexpected.
    """
    files = video.get("video_files") or []
    if not files:
        return None
    sortable = [
        (
            f.get("width") or 0,
            1 if (f.get("file_type") or "").endswith("mp4") else 0,
            f,
        )
        for f in files
        if f.get("link")
    ]
    sortable.sort(key=lambda t: (t[0], t[1]), reverse=True)
    for width, _mp4, f in sortable:
        if width <= 1920:
            return f
    return sortable[0][2]
