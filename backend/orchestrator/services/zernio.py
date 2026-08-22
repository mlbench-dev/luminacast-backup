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
            # Per Zernio's OpenAPI spec, POST /v1/posts has no "mediaUrls"
            # field at all — media is "mediaItems": [{type, url}]. The old
            # payload silently attached nothing (Zernio ignored the unknown
            # key), so every post arrived as text-only content and got
            # rejected with 400 by any platform that requires media (TikTok,
            # YouTube — exactly the "video is ready" flow this call is
            # always used for, so every render is a video item).
            payload["mediaItems"] = [
                {"type": "video", "url": url} for url in media_urls
            ]
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
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                logger.error(
                    "Zernio create_post failed (status=%s): %s",
                    resp.status_code, body,
                )
            resp.raise_for_status()
            data = resp.json()
            # Response nests everything under "post" (PostCreateResponse in
            # Zernio's OpenAPI spec) — data.get("id") always logged None.
            logger.info(
                "Zernio post created: %s", (data.get("post") or {}).get("_id"),
            )
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

    async def get_comments(self, post_id: str, account_id: str) -> list[dict[str, Any]]:
        """GET /v1/inbox/comments/{postId}?accountId=...

        Confirmed against the OpenAPI spec: comments live under the unified
        inbox, not a /posts/{id}/comments path. That path doesn't exist \u2014
        hitting it returned HTTP 200 with Zernio's own dashboard HTML (their
        Next.js app's catch-all route), which silently defeated
        resp.raise_for_status() (200 is "success") and only broke on
        resp.json() one line later. The caller swallowed that as a generic
        exception and logged a warning, so comments never synced and the UI
        just showed an empty "no comments" state with no visible error \u2014
        confirmed against a real TikTok post with an actual comment on it
        that never appeared. accountId is required by this endpoint (each
        platform connection has its own comment thread).
        """
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{ZERNIO_BASE}/inbox/comments/{post_id}",
                headers=self.headers,
                params={"accountId": account_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return (data.get("comments") or []) if isinstance(data, dict) else []

    async def reply_to_comment(
        self, post_id: str, account_id: str, message: str, comment_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/inbox/comments/{postId} \u2014 same corrected path/shape as
        get_comments above: accountId + message in the body, and the field
        is "message" not "content". comment_id is optional (omit to reply
        on the post itself rather than a specific comment)."""
        body: dict[str, Any] = {"accountId": account_id, "message": message}
        if comment_id:
            body["commentId"] = comment_id
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ZERNIO_BASE}/inbox/comments/{post_id}",
                headers=self.headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    # \u2500\u2500 Profiles / connected accounts \u2500\u2500

    async def list_profiles(self) -> list[dict[str, Any]]:
        """GET /v1/accounts — every social account connected under this
        platform-wide Zernio key.

        Confirmed live: Zernio enforces a hard 60-requests/minute limit on
        this key, shared across every Luminacast customer combined (page
        loads, connects, comment syncs, posts — all one pool). A short fixed
        backoff isn't enough to survive that: if the budget is genuinely
        exhausted, it doesn't recover until the minute actually rolls over,
        so a couple of quick retries can easily land entirely inside the
        same dead window. On a 429 specifically, wait for the real reset
        time Zernio reports (Retry-After, falling back to
        X-RateLimit-Reset) instead of guessing — bounded to 65s so a single
        call can't hang indefinitely. Non-429 failures (timeouts, momentary
        5xx) still use a short fixed backoff, mirroring the retry shape
        already used by get_recent_connection_error.
        """
        import asyncio
        import time

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                    resp = await client.get(f"{ZERNIO_BASE}/accounts", headers=self.headers)
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, dict) and "accounts" in data:
                        return data["accounts"]
                    return data if isinstance(data, list) else []
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429 and attempt < 2:
                    wait = 5.0
                    retry_after = exc.response.headers.get("retry-after")
                    reset_at = exc.response.headers.get("x-ratelimit-reset")
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            pass
                    elif reset_at:
                        try:
                            wait = float(reset_at) - time.time()
                        except ValueError:
                            pass
                    wait = max(1.0, min(wait, 65.0))
                    logger.warning(
                        "Zernio list_profiles rate-limited (attempt %d/3), "
                        "waiting %.1fs for reset", attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.warning(
                    "Zernio list_profiles failed (attempt %d/3): %s", attempt + 1, exc,
                )
                if attempt < 2:
                    await asyncio.sleep(0.8)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Zernio list_profiles failed (attempt %d/3): %s", attempt + 1, exc,
                )
                if attempt < 2:
                    await asyncio.sleep(0.8)
        assert last_exc is not None
        raise last_exc

    async def disconnect_account(self, account_id: str) -> None:
        """Revoke a connected account on Zernio's side.

        Without this, "disconnect" only ever removed our local row —
        the account stayed connected on Zernio, so the next channel-list
        reconciliation (list_profiles) just recreated it as active again.

        A 404 here means the account is already gone on Zernio's side
        (e.g. a previous disconnect attempt got this far and our local row
        just never got updated to match) — that's the caller's desired end
        state already, not a failure, so it's swallowed rather than raised.
        """
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.delete(
                f"{ZERNIO_BASE}/accounts/{account_id}", headers=self.headers
            )
            if resp.status_code == 404:
                return
            resp.raise_for_status()

    async def _get_default_profile_id(self, client: httpx.AsyncClient) -> str:
        """Resolve the Zernio *workspace* profile id required by the connect
        flow. This is a distinct concept from the "profiles" this codebase's
        /api/social/profiles endpoint returns (which are actually connected
        social ACCOUNTS via GET /accounts) — Zernio's own /v1/profiles are
        workspaces/brands, and /v1/connect/{platform} requires one via
        ?profileId=. We use the account's default profile; Zernio creates
        one automatically for every account, so this should always resolve.
        """
        resp = await client.get(f"{ZERNIO_BASE}/profiles", headers=self.headers)
        resp.raise_for_status()
        profiles = (resp.json() or {}).get("profiles") or []
        if not profiles:
            raise RuntimeError("Zernio account has no profiles configured")
        default = next((p for p in profiles if p.get("isDefault")), profiles[0])
        return default["_id"]

    async def get_oauth_url(self, platform: str, redirect_uri: str) -> dict[str, Any]:
        """Ask Zernio for an OAuth authorization URL for a given platform.

        Per Zernio's OpenAPI spec (docs.zernio.com/api/openapi), this is
        ``GET /v1/connect/{platform}?profileId=...&redirect_url=...`` — NOT
        the ``POST /accounts/connect`` this previously called, which doesn't
        exist as a route at all (confirmed against the live API: both GET
        and POST to /accounts/connect return a bodyless 405). The returned
        authUrl is opened in a popup; Zernio handles the OAuth dance and
        redirects the popup back to redirect_uri once the platform account
        is linked.
        """
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            profile_id = await self._get_default_profile_id(client)
            resp = await client.get(
                f"{ZERNIO_BASE}/connect/{platform}",
                headers=self.headers,
                params={"profileId": profile_id, "redirect_url": redirect_uri},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_recent_connection_error(
        self, platform: str, within_seconds: int = 600,
    ) -> str | None:
        """Best-effort human-readable reason for a recent failed connect.

        The OAuth redirect back to our own callback only ever carries a
        generic code (``connection_failed``) — Zernio's real explanation
        (e.g. "We couldn't find a YouTube channel for the Google account
        you authorized...") only exists in their GET /v1/logs activity
        feed. We look up the most recent failed connection-log entry for
        this platform and return its message, but only if it's fresh
        enough to plausibly BE the attempt that just happened — otherwise
        a stale failure from an earlier session could get shown for an
        unrelated new attempt. Returns None (never raises) on any lookup
        failure so callers can fall back to the generic code.

        within_seconds defaults to 10 minutes rather than something
        tighter: confirmed live that a real, immediately-preceding retry
        (same account, same still-unresolved "no YouTube channel" cause)
        can land in Zernio's log several minutes "behind" the callback that
        looks it up — repeated attempts against an unchanged root cause
        don't reliably produce a fresh timestamped entry per click (Google
        may skip re-prompting for consent it's already granted, short-
        circuiting Zernio's own OAuth callback before it logs anything
        new). A wider window still protects against showing a truly
        unrelated failure from a much earlier session.

        Retries a few times with a short delay: the callback page calls
        this within ~1s of the OAuth redirect landing, and Zernio's log
        write for that same event may not be queryable that fast — an
        immediate single query can race ahead of their own ingestion.
        """
        import asyncio
        from datetime import datetime, timezone

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                    resp = await client.get(
                        f"{ZERNIO_BASE}/logs",
                        headers=self.headers,
                        params={
                            "type": "connections",
                            "platform": platform,
                            "status": "failed",
                            "limit": 1,
                            "days": 1,
                        },
                    )
                    resp.raise_for_status()
                    logs = (resp.json() or {}).get("logs") or []
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                logger.warning(
                    "Zernio get_recent_connection_error: log fetch failed "
                    "(attempt %d/3) for platform=%s: %s",
                    attempt + 1, platform, exc,
                )
                logs = []

            if logs:
                break
            if attempt < 2:
                await asyncio.sleep(0.8)
        else:
            logger.info(
                "Zernio get_recent_connection_error: no failed-connection "
                "log found for platform=%s after 3 attempts", platform,
            )

        if not logs:
            logger.info(
                "Zernio get_recent_connection_error: no logs for platform=%s",
                platform,
            )
            return None
        entry = logs[0]
        message = entry.get("error_message") or None
        if not message:
            logger.info(
                "Zernio get_recent_connection_error: log entry for "
                "platform=%s has no error_message: %r", platform, entry,
            )
            return None
        created_at = entry.get("created_at")
        if not created_at:
            logger.info(
                "Zernio get_recent_connection_error: using message for "
                "platform=%s (no created_at to check freshness)", platform,
            )
            return message
        try:
            # Zernio log timestamps look like "2026-08-03 09:26:38" (UTC).
            ts = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            return message
        age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        logger.info(
            "Zernio get_recent_connection_error: platform=%s latest failed "
            "log is %.1fs old (limit %ds): %r",
            platform, age_s, within_seconds, message,
        )
        if age_s > within_seconds:
            return None
        return message

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
