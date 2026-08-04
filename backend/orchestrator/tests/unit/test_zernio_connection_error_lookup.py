"""Tests for ZernioService.get_recent_connection_error.

Context: the OAuth redirect back to our own callback only ever carries a
generic error code (e.g. "connection_failed") — confirmed on a real failed
YouTube connect attempt, where the toast could only ever say
"connection_failed" with nothing actionable. The real explanation (e.g.
"We couldn't find a YouTube channel for the Google account you
authorized...") only exists in Zernio's GET /v1/logs activity feed, found
by querying it directly with type=connections&platform=youtube&status=failed.

This adds a lookup that fetches that real reason, but only trusts it if the
log entry is recent enough to plausibly be the attempt that just happened —
otherwise a stale failure from a much earlier session could get shown for
an unrelated new attempt.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

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


def _fake_client(logs, captured_params):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            captured_params.update(params or {})
            return _FakeResponse({"logs": logs})

    return _Client()


def test_returns_message_for_recent_failure(monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
    captured: dict = {}
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient",
        lambda *a, **k: _fake_client(
            [{"error_message": "no YouTube channel on this Google account", "created_at": recent}],
            captured,
        ),
    )
    svc = ZernioService(api_key="test-key")
    result = _run(svc.get_recent_connection_error("youtube"))
    assert result == "no YouTube channel on this Google account"
    assert captured["platform"] == "youtube"
    assert captured["status"] == "failed"
    assert captured["type"] == "connections"


def test_ignores_stale_failure(monkeypatch):
    """A failure from an hour ago must not be attributed to a fresh attempt."""
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    captured: dict = {}
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient",
        lambda *a, **k: _fake_client(
            [{"error_message": "old unrelated failure", "created_at": stale}],
            captured,
        ),
    )
    svc = ZernioService(api_key="test-key")
    result = _run(svc.get_recent_connection_error("youtube", within_seconds=120))
    assert result is None


def test_no_logs_returns_none(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient",
        lambda *a, **k: _fake_client([], captured),
    )
    svc = ZernioService(api_key="test-key")
    result = _run(svc.get_recent_connection_error("instagram"))
    assert result is None


def test_network_failure_returns_none_not_raise(monkeypatch):
    class _BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        "services.zernio.httpx.AsyncClient", lambda *a, **k: _BrokenClient(),
    )
    svc = ZernioService(api_key="test-key")
    result = _run(svc.get_recent_connection_error("tiktok"))
    assert result is None
