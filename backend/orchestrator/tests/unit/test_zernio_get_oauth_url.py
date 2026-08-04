"""Regression test for ZernioService.get_oauth_url.

Bug: the "Connect platform" button (/api/social/connect) called
POST https://zernio.com/api/v1/accounts/connect with a JSON body
{"platform": ..., "redirectUri": ...} and always got back a bodyless 405 —
confirmed directly against the live Zernio API (both GET and POST to that
path 405, with no Allow header, meaning the route doesn't exist at all).

Per Zernio's own OpenAPI spec (docs.zernio.com/api/openapi), the real route
is ``GET /v1/connect/{platform}`` with query params ``profileId`` (a
Zernio *workspace* id from GET /v1/profiles — a different concept from the
connected social ACCOUNTS this codebase's /api/social/profiles endpoint
returns via GET /accounts) and ``redirect_url``. Confirmed against the live
API: the corrected request returns a real authUrl.
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
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"method": "GET", "url": url, "params": params})
        if url.endswith("/profiles"):
            return _FakeResponse({
                "profiles": [
                    {"_id": "prof_other", "isDefault": False},
                    {"_id": "prof_default", "isDefault": True},
                ],
            })
        return _FakeResponse({"authUrl": "https://example.com/oauth", "state": "s"})

    async def post(self, *a, **k):
        raise AssertionError("get_oauth_url must not POST — the real route is GET")


def test_get_oauth_url_hits_connect_platform_path_with_query_params(monkeypatch):
    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient", lambda *a, **k: fake_client,
    )

    svc = ZernioService(api_key="test-key")
    result = _run(svc.get_oauth_url("tiktok", "https://example.com/callback"))

    assert result == {"authUrl": "https://example.com/oauth", "state": "s"}
    # First call resolves the workspace profile id, second is the actual connect.
    profile_call, connect_call = fake_client.calls
    assert profile_call["url"].endswith("/profiles")
    assert connect_call["url"].endswith("/connect/tiktok")
    assert connect_call["params"] == {
        "profileId": "prof_default",
        "redirect_url": "https://example.com/callback",
    }


def test_get_oauth_url_uses_default_profile_not_first_in_list(monkeypatch):
    """isDefault must win even when it isn't the first entry returned."""
    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient", lambda *a, **k: fake_client,
    )
    svc = ZernioService(api_key="test-key")
    _run(svc.get_oauth_url("instagram", "https://example.com/callback"))
    _, connect_call = fake_client.calls
    assert connect_call["params"]["profileId"] == "prof_default"


def test_get_oauth_url_raises_clearly_when_no_profiles_exist(monkeypatch):
    class _EmptyProfilesClient(_FakeAsyncClient):
        async def get(self, url, headers=None, params=None):
            if url.endswith("/profiles"):
                return _FakeResponse({"profiles": []})
            raise AssertionError("must not reach connect call with no profile")

    fake_client = _EmptyProfilesClient()
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient", lambda *a, **k: fake_client,
    )
    svc = ZernioService(api_key="test-key")
    with pytest.raises(RuntimeError, match="no profiles"):
        _run(svc.get_oauth_url("tiktok", "https://example.com/callback"))
