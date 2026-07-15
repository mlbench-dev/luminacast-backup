"""Apify TikTok scraping service adapter."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import time

import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)


def _log(level: str, service: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": service,
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


async def _retry_async(func, *args, max_retries=3, base_delay=2.0, **kwargs):
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            _log(
                "warning",
                func.__module__ or "service",
                f"Retry {attempt + 1}/{max_retries}: {e}",
                delay=delay,
            )
            await asyncio.sleep(delay)


class ApifyTikTokService:
    BASE_URL = "https://api.apify.com/v2"
    ACTOR_ID = "clockworks~tiktok-scraper"
    POLL_INTERVAL_SECONDS = 5
    MAX_POLL_SECONDS = 120

    def __init__(self):
        self.api_token = settings.APIFY_API_TOKEN

    def _params(self) -> dict:
        return {"token": self.api_token}

    @staticmethod
    def _extract_username(tiktok_url: str) -> str:
        """Extract bare username from a TikTok profile URL or handle.

        The Apify clockworks/tiktok-scraper `profiles` field expects plain
        usernames (e.g. "mrbeast"), NOT full URLs.  Previous code passed the
        full URL which caused the actor to ignore the profile and return
        stale/default results — root cause of B-033.
        """
        # Match @username in a URL like https://www.tiktok.com/@username/...
        m = re.search(r'tiktok\.com/@([^/?&#]+)', tiktok_url)
        if m:
            return m.group(1)
        # Already a bare handle (possibly with @)
        return tiktok_url.lstrip("@").split("/")[0].split("?")[0].strip()

    async def _start_run(self, tiktok_url: str, max_videos: int) -> str:
        """Start an Apify actor run. Returns run_id."""
        username = self._extract_username(tiktok_url)
        _log(
            "info",
            "apify_tiktok",
            "Extracted username for Apify profiles field",
            raw_url=tiktok_url,
            username=username,
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/acts/{self.ACTOR_ID}/runs",
                params=self._params(),
                json={
                    "profiles": [username],
                    "resultsPerPage": max_videos,
                    "shouldDownloadVideos": False,
                    "shouldDownloadCovers": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["data"]["id"]

    async def _get_run_status(self, run_id: str) -> dict:
        """Get run status. Returns run data dict."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/actor-runs/{run_id}",
                params=self._params(),
            )
            response.raise_for_status()
            return response.json()["data"]

    async def _get_dataset_items(self, dataset_id: str) -> list:
        """Fetch dataset items from a completed run."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/datasets/{dataset_id}/items",
                params={**self._params(), "format": "json", "clean": "true"},
            )
            response.raise_for_status()
            return response.json()

    def _normalize_item(self, item: dict) -> dict:
        """Normalize an Apify TikTok item to our schema."""
        # Extract cover image URL from videoMeta
        video_meta = item.get("videoMeta") or {}
        cover_url = video_meta.get("coverUrl", "") if isinstance(video_meta, dict) else ""

        # Extract author info
        author_meta = item.get("authorMeta") or {}
        author_avatar = ""
        if isinstance(author_meta, dict):
            author_avatar = author_meta.get("avatarLarger", "") or author_meta.get("avatar", "")

        # Extract direct video download URL (for face extraction / voice cloning)
        video_download_url = ""
        if isinstance(video_meta, dict):
            video_download_url = (
                video_meta.get("downloadAddr", "")
                or video_meta.get("playAddr", "")
                or video_meta.get("url", "")
            )
        # mediaUrls often contains the direct .mp4 CDN link
        media_urls = item.get("mediaUrls", [])
        if not video_download_url and media_urls:
            for mu in media_urls:
                if isinstance(mu, str) and ("video" in mu or ".mp4" in mu or "tiktokcdn" in mu):
                    video_download_url = mu
                    break

        # Duration from videoMeta
        duration = 0
        if isinstance(video_meta, dict):
            duration = video_meta.get("duration", 0) or 0

        return {
            "video_url": item.get("webVideoUrl") or item.get("videoUrl") or item.get("video_url") or "",
            "video_download_url": video_download_url,  # direct .mp4 CDN URL for download
            "description": item.get("text") or item.get("description") or "",
            "cover_url": cover_url,
            "duration": duration,
            "author_avatar": author_avatar,
            "author_name": author_meta.get("nickName", "") if isinstance(author_meta, dict) else "",
            "media_urls": media_urls,
            "stats": {
                "likes": item.get("diggCount") or item.get("likes") or 0,
                "comments": item.get("commentCount") or item.get("comments") or 0,
                "shares": item.get("shareCount") or item.get("shares") or 0,
                "views": item.get("playCount") or item.get("views") or 0,
            },
        }

    async def fetch_tiktok_videos(
        self, tiktok_url: str, max_videos: int = 5
    ) -> Optional[list[dict]]:
        """Fetch TikTok videos via Apify. Returns list of video data or None on failure."""
        with sentry_sdk.start_span(op='apify', description='TikTok video fetch') as span:
            span.set_data('url', tiktok_url)
            span.set_data('max_videos', max_videos)
            _log(
                "info",
                "apify_tiktok",
                "Starting TikTok scrape",
                tiktok_url=tiktok_url,
                max_videos=max_videos,
            )

            _apify_start = time.monotonic()
            try:
                run_id = await _retry_async(self._start_run, tiktok_url, max_videos)
                _log("info", "apify_tiktok", "Apify run started", run_id=run_id)

                # Poll for completion
                elapsed = 0
                while elapsed < self.MAX_POLL_SECONDS:
                    await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
                    elapsed += self.POLL_INTERVAL_SECONDS

                    run_data = await _retry_async(self._get_run_status, run_id)
                    status = run_data.get("status", "")

                    _log(
                        "info",
                        "apify_tiktok",
                        "Polling run status",
                        run_id=run_id,
                        status=status,
                        elapsed=elapsed,
                    )

                    if status == "SUCCEEDED":
                        dataset_id = run_data["defaultDatasetId"]
                        raw_items = await _retry_async(self._get_dataset_items, dataset_id)
                        videos = [self._normalize_item(item) for item in raw_items]

                        span.set_data('video_count', len(videos))
                        _log(
                            "info",
                            "apify_tiktok",
                            "TikTok scrape complete",
                            run_id=run_id,
                            video_count=len(videos),
                        )
                        try:
                            from services.usage_logger import log_api_usage
                            await log_api_usage(
                                user_id="", service="apify_scrape", operation="tiktok_videos",
                                success=True,
                                duration_seconds=round(time.monotonic() - _apify_start, 1),
                                cost_cents=max(1, len(videos)),  # ~$0.002/video
                            )
                        except Exception:
                            pass
                        return videos

                    if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                        span.set_data('status', status)
                        _log(
                            "warning",
                            "apify_tiktok",
                            "Apify run ended with non-success status",
                            run_id=run_id,
                            status=status,
                        )
                        return None

                _log(
                    "warning",
                    "apify_tiktok",
                    "Apify run polling timed out",
                    run_id=run_id,
                    elapsed=elapsed,
                )
                return None

            except Exception as e:
                _log(
                    "error",
                    "apify_tiktok",
                    f"TikTok scrape failed: {e}",
                    tiktok_url=tiktok_url,
                )
                return None


_instance: ApifyTikTokService | None = None


def get_apify_tiktok_service() -> ApifyTikTokService:
    global _instance
    if _instance is None:
        _instance = ApifyTikTokService()
    return _instance
