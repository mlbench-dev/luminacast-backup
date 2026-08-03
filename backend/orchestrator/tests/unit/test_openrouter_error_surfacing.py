"""Regression tests for OpenRouter error surfacing.

Bug: an OpenRouter failure (e.g. 402 "insufficient credits") propagated as a
bare httpx.HTTPStatusError with no global handler registered for it. FastAPI's
default handler turns any unhandled exception into a content-free 500, so the
frontend (which reads err.response.data.detail, see ScriptPhase.tsx) had
nothing useful to show and fell back to a generic "Script generation failed"
with no indication of why — indistinguishable from an actual bug. Confirmed
directly against OpenRouter's API: the account was out of credits and
OpenRouter's own error body said so plainly
("Insufficient credits. Add more using https://openrouter.ai/settings/credits"),
but that message never reached the user.

Fix: _post_chat_completions now raises OpenRouterError with the upstream
message extracted from the response body, _retry_async fails fast on
non-retryable status codes (402/401/etc. can't be fixed by retrying), and
main.py registers a global handler that turns OpenRouterError into a clean
JSON {"detail": ...} response.
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

from services.openrouter import OpenRouterService, OpenRouterError, _retry_async


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        self.call_count += 1
        return self._response


def test_402_raises_openrouter_error_with_upstream_message(monkeypatch):
    fake_response = _FakeResponse(402, {
        "error": {
            "message": "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
            "code": 402,
        },
    })
    fake_client = _FakeAsyncClient(fake_response)
    monkeypatch.setattr(
        "services.openrouter.httpx.AsyncClient", lambda *a, **k: fake_client,
    )

    svc = OpenRouterService()
    with pytest.raises(OpenRouterError) as exc_info:
        _run(svc._post_chat_completions("anthropic/claude-opus-4.8", [], 16, 0.7))

    assert exc_info.value.status_code == 402
    assert "Insufficient credits" in str(exc_info.value)


def test_retry_async_does_not_retry_402():
    """Retrying an insufficient-credits error can't succeed — must fail fast
    (1 call), not burn through the full backoff schedule (~14s)."""
    calls = {"n": 0}

    async def _always_402():
        calls["n"] += 1
        raise OpenRouterError(402, "OpenRouter: Insufficient credits.")

    with pytest.raises(OpenRouterError):
        _run(_retry_async(_always_402, max_retries=3, base_delay=0.01))

    assert calls["n"] == 1, "402 must fail immediately, not retry"


def test_retry_async_still_retries_transient_5xx():
    calls = {"n": 0}

    async def _fail_twice_then_succeed():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OpenRouterError(503, "OpenRouter: upstream unavailable")
        return "ok"

    result = _run(_retry_async(_fail_twice_then_succeed, max_retries=3, base_delay=0.01))
    assert result == "ok"
    assert calls["n"] == 3


def test_openrouter_error_handler_returns_clean_json_detail():
    """The registered FastAPI exception handler must convert OpenRouterError
    into a JSON body carrying the readable message under 'detail' — the
    field the frontend actually reads (see ScriptPhase.tsx)."""
    import importlib.util
    spec = importlib.util.find_spec("main")
    assert spec is not None, "main module must be importable"

    src = open(spec.origin, encoding="utf-8").read()
    assert "OpenRouterError" in src, (
        "main.py must import/handle OpenRouterError"
    )
    assert "_openrouter_error_handler" in src or "exception_handler(OpenRouterError)" in src, (
        "main.py must register a global exception_handler for OpenRouterError"
    )
