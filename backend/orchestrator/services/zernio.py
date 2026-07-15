"""Zernio social-media wrapper.

Used to schedule and publish rendered casts across TikTok / Instagram /
YouTube / LinkedIn / Facebook, fetch comments, reply, and pull analytics.

Docs: https://docs.zernio.com  \u2014  API ref: https://docs.zernio.com/api/openapi

The wrapper is intentionally tiny and best-effort: a missing key raises a
clear runtime error, and individual API calls return None / raise on
network/HTTP errors so callers can decide whether to retry.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)


ZERNIO_BASE = "https://zernio.com/api/v1"
HTTP_TIMEOUT_SECONDS = 60.0
UPLOAD_TIMEOUT_SECONDS = 180.0


class ZernioService:
    """Async client for the Zernio REST API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.ZERNIO_API_KEY
        if not self.api_key:
            raise RuntimeError("ZERNIO_API_KEY not configured")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # \u2500\u2500 Posts \u2500\u2500

    async def create_post(
        self,
        content: str,
        platforms: list[dict[str, Any]],  # [{"platform": "tiktok", "accountId": "acc_xxx"}]
        media_urls: list[str] | None = None,
        scheduled_for: str | None = None,  # ISO 8601 or None for immediate
    ) -> dict[str, Any]:
        """Create or schedule a post across one or more platforms."""
        payload: dict[str, Any] = {"content": content, "platforms": platforms}
        if media_urls:
            payload["mediaUrls"] = media_urls
        if scheduled_for:
            payload["scheduledFor"] = scheduled_for
        else:
            payload["publishNow"] = True

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ZERNIO_BASE}/posts",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("Zernio post created: %s", data.get("id"))
            return data

    async def get_post(self, post_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{ZERNIO_BASE}/posts/{post_id}", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.delete(f"{ZERNIO_BASE}/posts/{post_id}", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    # \u2500\u2500 Comments \u2500\u2500

    async def get_comments(self, post_id: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{ZERNIO_BASE}/posts/{post_id}/comments",
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            # Zernio may return either a list or {comments: [...]} \u2014 normalize.
            if isinstance(data, dict) and "comments" in data:
                return data["comments"]
            return data if isinstance(data, list) else []

    async def reply_to_comment(
        self, post_id: str, comment_id: str, text: str
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ZERNIO_BASE}/posts/{post_id}/comments/{comment_id}/reply",
                headers=self.headers,
                json={"content": text},
            )
            resp.raise_for_status()
            return resp.json()

    # \u2500\u2500 Profiles / connected accounts \u2500\u2500

    async def list_profiles(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{ZERNIO_BASE}/accounts", headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "accounts" in data:
                return data["accounts"]
            return data if isinstance(data, list) else []

    async def get_oauth_url(self, platform: str, redirect_uri: str) -> dict[str, Any]:
        """Ask Zernio for an OAuth authorization URL for a given platform.

        Zernio exposes per-platform connect endpoints under /accounts/connect.
        The returned URL is opened in a popup; Zernio handles the OAuth dance
        and redirects the popup back to redirect_uri once the platform account
        is linked.
        """
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ZERNIO_BASE}/accounts/connect",
                headers=self.headers,
                json={"platform": platform, "redirectUri": redirect_uri},
            )
            resp.raise_for_status()
            return resp.json()

    async def create_profile(self, label: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ZERNIO_BASE}/accounts",
                headers=self.headers,
                json={"label": label},
            )
            resp.raise_for_status()
            return resp.json()

    # \u2500\u2500 Media \u2500\u2500

    async def upload_media(self, file_url: str) -> dict[str, Any]:
        \
        \
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ZERNIO_BASE}/media/upload",
                headers=self.headers,
                json={"url": file_url},
            )
            resp.raise_for_status()
            return resp.json()

    # \u2500\u2500 Analytics \u2500\u2500

    async def get_post_analytics(self, post_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{ZERNIO_BASE}/posts/{post_id}/analytics",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()


_singleton: ZernioService | None = None


def get_zernio_service() -> ZernioService | None:
    """Return a cached ZernioService or None when no API key is configured."""
    global _singleton
    if _singleton is not None:
        return _singleton
    if not settings.ZERNIO_API_KEY:
        return None
    try:
        _singleton = ZernioService(settings.ZERNIO_API_KEY)
        return _singleton
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("Zernio init failed: %s", exc)
        return None
