"""Regression tests for ZernioService.create_post.

Bug: "Post Now" always failed with a bare 400 from Zernio. Per Zernio's
OpenAPI spec (docs.zernio.com/api/openapi), POST /v1/posts has no
"mediaUrls" field at all — the real field is "mediaItems": [{type, url}].
The old payload sent mediaUrls, which Zernio silently ignored as an unknown
key, so every post arrived as text-only content with no media attached —
and any platform requiring media (TikTok, YouTube — exactly what this app
always posts) rejected it with 400.

Also: the 400's real reason lived in the response body, but the router
only ever surfaced str(httpx.HTTPStatusError) — a generic "Client error
'400 Bad Request' for url '...'" with no indication of what was actually
wrong. Fixed to extract body.error/body.message.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

from services.zernio import ZernioService


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=None, response=self,
            )

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "json": json})
        return self._response


def test_create_post_sends_media_items_not_media_urls(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(201, {"id": "post_123"}))
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient", lambda *a, **k: fake_client,
    )
    svc = ZernioService(api_key="test-key")
    result = _run(svc.create_post(
        content="check this out",
        platforms=[{"platform": "tiktok", "accountId": "acc_1"}],
        media_urls=["https://cdn.example.com/final.mp4"],
    ))
    assert result == {"id": "post_123"}
    payload = fake_client.calls[0]["json"]
    assert "mediaUrls" not in payload, (
        "mediaUrls isn't a real field in Zernio's API — it's silently ignored"
    )
    assert payload["mediaItems"] == [
        {"type": "video", "url": "https://cdn.example.com/final.mp4"},
    ]
    assert payload["publishNow"] is True


def test_create_post_400_raises_with_body_accessible(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(400, {"error": "content is required"}))
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient", lambda *a, **k: fake_client,
    )
    svc = ZernioService(api_key="test-key")
    with pytest.raises(Exception) as exc_info:
        _run(svc.create_post(
            content="",
            platforms=[{"platform": "tiktok", "accountId": "acc_1"}],
            media_urls=["https://cdn.example.com/final.mp4"],
        ))
    # The router's except block reads exc.response.json() — confirm that
    # path stays usable end to end.
    response = exc_info.value.response
    assert response.json() == {"error": "content is required"}


def test_router_extracts_real_error_body_not_bare_exception_str():
    """Static guard: the router must pull body.error/body.message out of a
    failed Zernio call instead of just stringifying the httpx exception,
    which never contains Zernio's actual validation reason."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "routers" / "social.py"
    text = src.read_text(encoding="utf-8")
    start = text.find("async def create_social_post")
    end = text.find("\n@router.", start + 1)
    body = text[start:end if end != -1 else len(text)]
    assert "response.json()" in body, (
        "must attempt to read the failed response body for the real reason"
    )
    assert 'body.get("error")' in body or 'body.get("message")' in body
