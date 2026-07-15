"""Unit tests for PR #69 — MuseTalk dispatch URL/payload/response fix.

Covers:
  - MuseTalk endpoint is /api/musetalk-lipsync (not /render)
  - Request body uses face_image_url (not image_url) + audio_url
  - Response shape {status, output_r2_key, duration_seconds, ...} is parsed
  - httpx.ConnectError("") still surfaces the type name to the caller
  - cast_render._resolve_video_bytes handles both R2-key and base64 shapes
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import patch

import pytest


# ─── Stub sentry_sdk so importing the modules doesn't pull in the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )


def _import_dispatcher():
    from services import render_dispatcher
    return render_dispatcher


def _run(coro):
    """Run a coroutine on a fresh loop for the duration of one test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: MuseTalk URL points to /api/musetalk-lipsync (not /render)
# ─────────────────────────────────────────────────────────────────────────────

def test_musetalk_url_points_to_correct_endpoint():
    """The dispatcher MUST POST to /api/musetalk-lipsync. The old /render
    path returned 404 on the real gpu-worker.
    """
    rd = _import_dispatcher()
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"status": "ok", "output_r2_key": "k.mp4", "duration_seconds": 1.0}

    class _FakeClient:
        def __init__(self, *_a, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResp()

    dispatcher = rd.RenderDispatcher()
    with patch.object(rd.httpx, "AsyncClient", _FakeClient):
        _run(dispatcher._render_on_musetalk("http://x/img.png", "http://x/a.wav"))

    assert "/api/musetalk-lipsync" in captured["url"], captured
    assert "/render" not in captured["url"].rsplit("/", 1)[-1], captured


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2: Request payload uses face_image_url (not image_url)
# ─────────────────────────────────────────────────────────────────────────────

def test_musetalk_payload_uses_face_image_url():
    """Real endpoint validates `face_image_url` and 422s on the legacy
    `image_url` field. Must also pass audio_url + render_size + fps.
    """
    rd = _import_dispatcher()
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"status": "ok", "output_r2_key": "k.mp4", "duration_seconds": 1.0}

    class _FakeClient:
        def __init__(self, *_a, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, json=None):
            captured["json"] = json
            return _FakeResp()

    dispatcher = rd.RenderDispatcher()
    with patch.object(rd.httpx, "AsyncClient", _FakeClient):
        _run(dispatcher._render_on_musetalk("http://x/img.png", "http://x/a.wav"))

    body = captured["json"]
    assert "face_image_url" in body, body
    assert "image_url" not in body, body
    assert body["audio_url"] == "http://x/a.wav"
    assert "render_size" in body and "fps" in body


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3: R2-key response shape is parsed (no more base64 of raw mp4 bytes)
# ─────────────────────────────────────────────────────────────────────────────

def test_musetalk_parses_r2_key_response():
    """Real endpoint returns JSON {status, output_r2_key, duration_seconds, ...}.
    The dispatcher must surface {output_r2_key, duration_s}.
    """
    rd = _import_dispatcher()

    class _FakeResp:
        status_code = 200
        def json(self):
            return {
                "status": "ok",
                "output_r2_key": "musetalk_outputs/abc.mp4",
                "duration_seconds": 4.5,
                "render_seconds": 12.0,
            }

    class _FakeClient:
        def __init__(self, *_a, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, json=None):
            return _FakeResp()

    dispatcher = rd.RenderDispatcher()
    with patch.object(rd.httpx, "AsyncClient", _FakeClient):
        result = _run(dispatcher._render_on_musetalk("u", "a"))

    assert result["output_r2_key"] == "musetalk_outputs/abc.mp4"
    assert result["duration_s"] == 4.5
    # And it must NOT silently report a "video" key — that field is gone.
    assert "video" not in result


def test_musetalk_rejects_bad_status_in_response():
    """If the worker returns a JSON body without a successful status or
    without an output_r2_key, we must raise RuntimeError rather than
    return a half-populated dict.
    """
    rd = _import_dispatcher()

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"status": "error", "detail": "no face detected"}

    class _FakeClient:
        def __init__(self, *_a, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, json=None):
            return _FakeResp()

    dispatcher = rd.RenderDispatcher()
    with patch.object(rd.httpx, "AsyncClient", _FakeClient):
        with pytest.raises(RuntimeError, match="bad response"):
            _run(dispatcher._render_on_musetalk("u", "a"))


# ─────────────────────────────────────────────────────────────────────────────
# Fix 4: httpx.ConnectError("") surfaces the type name to the caller
# ─────────────────────────────────────────────────────────────────────────────

def test_musetalk_connect_error_surfaces_type():
    """str(httpx.ConnectError("")) is "" — the wrapper must include the
    exception's class name so downstream logs / block_statuses[i].error
    are not just empty strings.
    """
    rd = _import_dispatcher()

    class _FakeClient:
        def __init__(self, *_a, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, json=None):
            raise rd.httpx.ConnectError("")

    dispatcher = rd.RenderDispatcher()
    with patch.object(rd.httpx, "AsyncClient", _FakeClient):
        with pytest.raises(RuntimeError) as ei:
            _run(dispatcher._render_on_musetalk("u", "a"))

    msg = str(ei.value)
    assert "ConnectError" in msg, msg
    assert "MuseTalk connect failed" in msg, msg


# ─────────────────────────────────────────────────────────────────────────────
# Fix 5: cast_render._resolve_video_bytes handles both shapes
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_video_bytes_handles_r2_key_and_b64():
    """The helper must accept (a) the new R2-key shape produced by the
    MuseTalk path and (b) the legacy base64 shape used by InfiniteTalk.

    tasks.cast_render imports Celery + SQLAlchemy + the full project app
    on module load, which we can't pay for in a unit test. Instead, parse
    the helper definition out of the file and exec it against a tiny
    namespace that satisfies its `httpx`, `base64`, and `sentry_sdk` refs.
    """
    import ast as _ast
    import os as _os
    import base64 as _b64
    here = _os.path.dirname(__file__)
    src_path = _os.path.normpath(_os.path.join(
        here, "..", "..", "tasks", "cast_render.py"
    ))
    with open(src_path) as _f:
        tree = _ast.parse(_f.read())
    fn_node = next(
        n for n in tree.body
        if isinstance(n, _ast.FunctionDef) and n.name == "_resolve_video_bytes"
    )
    module = _ast.Module(body=[fn_node], type_ignores=[])
    ns: dict = {}
    fake_httpx = types.SimpleNamespace()
    exec(compile(module, src_path, "exec"), {
        "httpx": fake_httpx,
        "base64": _b64,
        "sentry_sdk": sys.modules["sentry_sdk"],
    }, ns)
    resolve = ns["_resolve_video_bytes"]

    # R2-key shape: helper fetches https://media.luminacast.com/<key>
    class _FakeResp:
        content = b"\x00\x01R2-MP4\x02"
        def raise_for_status(self): pass

    captured = {}
    def _fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResp()
    fake_httpx.get = _fake_get

    out = resolve({"output_r2_key": "musetalk_outputs/x.mp4"})
    assert out == b"\x00\x01R2-MP4\x02"
    assert "media.luminacast.com" in captured["url"]
    assert "musetalk_outputs/x.mp4" in captured["url"]

    # Base64 shape: helper decodes inline
    raw = b"hello-mp4"
    enc = _b64.b64encode(raw).decode("ascii")
    assert resolve({"video": enc}) == raw

    # Unknown shape: raises
    with pytest.raises(RuntimeError, match="Unknown bake result"):
        resolve({"foo": "bar"})


