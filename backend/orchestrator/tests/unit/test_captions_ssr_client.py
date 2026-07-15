"""Step 12 — unit tests for the SSR caption-overlay HTTP client.

Pure unit tests (no DB, no network): every HTTP call is mocked. We cover the
four failure modes the composer relies on to fall back to drawtext —
HTTP error, malformed JSON, timeout, network failure — plus the success path
and the env-driven config knobs. Every failure must return ``None`` (the
composer's fallback signal) and capture to Sentry.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services import captions_ssr_client as ssr
from services.captions_ssr_client import SSROverlay, render_overlay

TOKENS = [
    {"text": "Hello", "startMs": 0, "endMs": 500},
    {"text": "world", "startMs": 500, "endMs": 1000},
]

OK_BODY = {
    "outputPath": "/tmp/captions_ssr/abc-123",
    "format": "png-sequence",
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "durationInFrames": 30,
    "elapsedMs": 1234,
}


def _mock_client(*, response=None, raise_exc=None):
    """Build a MagicMock standing in for httpx.Client used as a context mgr."""
    client = MagicMock()
    if raise_exc is not None:
        client.post.side_effect = raise_exc
    else:
        client.post.return_value = response
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx, client


def _make_response(status_code=200, json_body=None, text="", json_raises=False):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_raises:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_body
    return resp


def test_success_returns_overlay():
    resp = _make_response(200, OK_BODY)
    ctx, client = _mock_client(response=resp)
    with patch.object(ssr.httpx, "Client", return_value=ctx), \
            patch.object(ssr.sentry_sdk, "capture_exception") as cap:
        out = render_overlay(TOKENS, "hormozi_bold", 1080, 1920)

    assert isinstance(out, SSROverlay)
    assert out.output_path == Path("/tmp/captions_ssr/abc-123")
    assert out.format == "png-sequence"
    assert out.fps == 30
    assert out.duration_in_frames == 30
    cap.assert_not_called()
    # Verify the request shape the SSR service expects.
    _, kwargs = client.post.call_args
    body = kwargs["json"]
    assert body["presetId"] == "hormozi_bold"
    assert body["canvas"] == {"width": 1080, "height": 1920, "fps": 30}
    assert body["tokens"] == TOKENS


def test_empty_tokens_short_circuits():
    # No HTTP call should be made for an empty token list.
    with patch.object(ssr.httpx, "Client") as client_cls:
        out = render_overlay([], "hormozi_bold", 1080, 1920)
    assert out is None
    client_cls.assert_not_called()


def test_http_error_returns_none_and_captures():
    resp = _make_response(500, json_body=None, text="caption overlay render failed")
    ctx, _ = _mock_client(response=resp)
    with patch.object(ssr.httpx, "Client", return_value=ctx), \
            patch.object(ssr.sentry_sdk, "capture_exception") as cap:
        out = render_overlay(TOKENS, "hormozi_bold", 1080, 1920)
    assert out is None
    cap.assert_called_once()


def test_malformed_json_returns_none_and_captures():
    resp = _make_response(200, json_raises=True)
    ctx, _ = _mock_client(response=resp)
    with patch.object(ssr.httpx, "Client", return_value=ctx), \
            patch.object(ssr.sentry_sdk, "capture_exception") as cap:
        out = render_overlay(TOKENS, "hormozi_bold", 1080, 1920)
    assert out is None
    cap.assert_called_once()


def test_missing_output_path_returns_none_and_captures():
    resp = _make_response(200, {"format": "png-sequence"})  # no outputPath
    ctx, _ = _mock_client(response=resp)
    with patch.object(ssr.httpx, "Client", return_value=ctx), \
            patch.object(ssr.sentry_sdk, "capture_exception") as cap:
        out = render_overlay(TOKENS, "hormozi_bold", 1080, 1920)
    assert out is None
    cap.assert_called_once()


def test_timeout_returns_none_and_captures():
    ctx, _ = _mock_client(raise_exc=httpx.TimeoutException("timed out"))
    with patch.object(ssr.httpx, "Client", return_value=ctx), \
            patch.object(ssr.sentry_sdk, "capture_exception") as cap:
        out = render_overlay(TOKENS, "hormozi_bold", 1080, 1920)
    assert out is None
    cap.assert_called_once()


def test_network_failure_returns_none_and_captures():
    ctx, _ = _mock_client(raise_exc=httpx.ConnectError("connection refused"))
    with patch.object(ssr.httpx, "Client", return_value=ctx), \
            patch.object(ssr.sentry_sdk, "capture_exception") as cap:
        out = render_overlay(TOKENS, "hormozi_bold", 1080, 1920)
    assert out is None
    cap.assert_called_once()


def test_env_overrides_url_timeout_format(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SSR_URL", "http://example:9999/")
    monkeypatch.setenv("CAPTIONS_SSR_TIMEOUT_S", "42")
    monkeypatch.setenv("CAPTIONS_SSR_FORMAT", "alpha-video")

    assert ssr._ssr_url() == "http://example:9999"
    assert ssr._ssr_timeout_s() == 42.0
    assert ssr._ssr_format() == "alpha-video"

    resp = _make_response(200, {**OK_BODY, "format": "alpha-video"})
    ctx, client = _mock_client(response=resp)
    captured_timeout = {}

    def _client_factory(*args, **kwargs):
        captured_timeout["timeout"] = kwargs.get("timeout")
        return ctx

    with patch.object(ssr.httpx, "Client", side_effect=_client_factory):
        render_overlay(TOKENS, "hormozi_bold", 1080, 1920)

    assert captured_timeout["timeout"] == 42.0
    _, kwargs = client.post.call_args
    assert kwargs["json"]["format"] == "alpha-video"
    # URL is honoured.
    assert client.post.call_args[0][0] == "http://example:9999/render"


def test_bad_timeout_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SSR_TIMEOUT_S", "not-a-number")
    assert ssr._ssr_timeout_s() == ssr.DEFAULT_TIMEOUT_S


def test_block_overrides_forwarded():
    resp = _make_response(200, OK_BODY)
    ctx, client = _mock_client(response=resp)
    block = {"pageDurationInMilliseconds": 1200, "maxLines": 2}
    with patch.object(ssr.httpx, "Client", return_value=ctx):
        render_overlay(TOKENS, "karaoke_pop", 720, 1280, fps=24, block=block)
    _, kwargs = client.post.call_args
    body = kwargs["json"]
    assert body["block"] == block
    assert body["canvas"]["fps"] == 24
    assert body["presetId"] == "karaoke_pop"
