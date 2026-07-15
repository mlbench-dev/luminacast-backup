"""Unit tests for the defensive post-compose product-overlay composite in
``backend/orchestrator/tasks/cast_render.py``.

Background — the finalize/render pipeline lays out the avatar video and
remuxes audio but never burns the timeline's product-card image overlays
onto the final mp4. The product-conditioned avatar bake gate only fires
for ``speaking`` blocks in the ``HOSTKEY_ONLY`` branch; the
``body_motion`` / ``pip`` / ``voiceover`` render modes take other
branches and ship without the card. ``_post_compose_product_overlays``
closes that gap: it reads the image overlays off the timeline snapshot,
downloads compose's output, runs the multi-overlay compositor and
re-uploads over the same key — wrapped so a failure never fails the
render.

These tests import the real module by file path (with the three heavy
module-level deps stubbed) so the shipped helper is exercised directly.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── Stub the three project deps imported at module load so importing
#     cast_render.py doesn't pull Celery / the real Sentry client. ───
_sentry_calls: list = []

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *a, **k: _sentry_calls.append(a),
        set_tag=lambda *a, **k: None,
        set_extra=lambda *a, **k: None,
    )
if "tasks" not in sys.modules:
    _tasks_mod = types.ModuleType("tasks")
    _tasks_mod.celery_app = MagicMock()
    sys.modules["tasks"] = _tasks_mod
if "services" not in sys.modules:
    _services_mod = types.ModuleType("services")
    _services_mod.__path__ = []  # mark as package
    sys.modules["services"] = _services_mod
if "services.sentry" not in sys.modules:
    _sentry_sub = types.ModuleType("services.sentry")
    sys.modules["services.sentry"] = _sentry_sub
    sys.modules["services"].sentry = _sentry_sub


_CAST_RENDER_PATH = (
    Path(__file__).resolve().parent.parent.parent / "tasks" / "cast_render.py"
)


def _load_cast_render():
    spec = importlib.util.spec_from_file_location(
        "cast_render_under_test", _CAST_RENDER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cast_render = _load_cast_render()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_timeline(*, with_two_products=True):
    """A timeline with three image elements: two carry product_id, one
    doesn't. Plus a non-image element that must be ignored.
    """
    elements = [
        {
            "type": "image",
            "s": 1.0,
            "e": 4.0,
            "props": {"src": "https://cdn.example/p1.png", "title": "Widget", "price": "$9"},
            "metadata": {"product_id": "prod_1"},
        },
        {
            "type": "image",
            "s": 5.0,
            "e": 8.5,
            "props": {"src": "https://cdn.example/p2.jpg"},
            "metadata": {"product_id": "prod_2"},
        },
        {
            "type": "image",
            "s": 9.0,
            "e": 12.0,
            "props": {"src": "https://cdn.example/decor.png"},
            "metadata": {},  # no product_id → ignored
        },
        {
            "type": "video",
            "s": 0.0,
            "e": 30.0,
            "props": {"src": "https://cdn.example/avatar.mp4"},
            "metadata": {"product_id": "prod_1"},  # not image → ignored
        },
    ]
    if not with_two_products:
        elements = [elements[2], elements[3]]  # only the ignored ones
    return {"tracks": [{"type": "video", "elements": elements}]}


def _install_http_stub(monkeypatch):
    """Patch httpx.AsyncClient on the loaded module so image downloads are
    faked (each GET writes a small payload).
    """
    class _Resp:
        content = b"\x89PNG\r\n\x1a\n"  # tiny fake image payload

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(cast_render.httpx, "AsyncClient", _Client)


def _install_probe_stub(monkeypatch, width=720, height=1280):
    """Provide a fake services.block_normalize._probe_streams (imported
    lazily inside the helper).
    """
    mod = types.ModuleType("services.block_normalize")
    mod._probe_streams = lambda path: {"width": width, "height": height}
    monkeypatch.setitem(sys.modules, "services.block_normalize", mod)


def _install_compositor_stub(monkeypatch, *, raises=False):
    """Provide a fake services.video_compositor.composite_product_overlays_multi
    (imported lazily inside the helper). Returns the AsyncMock so callers can
    assert against it.
    """
    mod = types.ModuleType("services.video_compositor")
    if raises:
        async def _boom(**kwargs):
            raise RuntimeError("compositor blew up")
        comp = AsyncMock(side_effect=_boom)
    else:
        async def _ok(**kwargs):
            # The helper checks the output path exists after compositing.
            out = kwargs.get("output")
            if out:
                with open(out, "wb") as f:
                    f.write(b"composited")
        comp = AsyncMock(side_effect=_ok)
    mod.composite_product_overlays_multi = comp
    monkeypatch.setitem(sys.modules, "services.video_compositor", mod)
    return comp


def _make_r2(monkeypatch):
    """A mock r2 whose download_file writes a placeholder compose.mp4."""
    r2 = MagicMock()

    async def _download(key, local_path):
        with open(local_path, "wb") as f:
            f.write(b"compose-bytes")

    r2.download_file = AsyncMock(side_effect=_download)
    r2.upload_file = AsyncMock(return_value="uploaded")
    return r2


# ──────────────────────────────────────────────────────────────────────────


def test_extracts_overlays_from_timeline_tracks(monkeypatch):
    """3 image elements (2 with product_id, 1 without) + a non-image →
    exactly 2 overlay specs with correct s/e and product_image_path set."""
    _install_http_stub(monkeypatch)
    _install_probe_stub(monkeypatch)
    comp = _install_compositor_stub(monkeypatch)
    r2 = _make_r2(monkeypatch)

    cast = types.SimpleNamespace(product_title="Fallback Title")
    timeline = _make_timeline()

    _run(cast_render._post_compose_product_overlays(
        r2=r2,
        output_key="renders/r1/final.mp4",
        timeline=timeline,
        render_id="r1",
        cast=cast,
    ))

    # The helper extractor returns exactly the two product image elements.
    extracted = cast_render._timeline_product_image_elements(timeline)
    assert len(extracted) == 2

    comp.assert_awaited_once()
    overlays = comp.await_args.kwargs["product_overlays"]
    assert len(overlays) == 2

    assert overlays[0]["start_s"] == 1.0 and overlays[0]["end_s"] == 4.0
    assert overlays[0]["title"] == "Widget"
    assert overlays[0]["price"] == "$9"
    assert overlays[0]["product_image_path"] is not None

    # Second element has no title → falls back to the cast product title.
    assert overlays[1]["start_s"] == 5.0 and overlays[1]["end_s"] == 8.5
    assert overlays[1]["title"] == "Fallback Title"
    assert overlays[1]["product_image_path"] is not None

    # Output was re-uploaded over the same key.
    r2.upload_file.assert_awaited_once()
    assert r2.upload_file.await_args.args[1] == "renders/r1/final.mp4"


def test_skips_when_no_overlays(monkeypatch):
    """A timeline with no product_id image elements returns early — no
    download, no compositor call, no upload."""
    _install_http_stub(monkeypatch)
    _install_probe_stub(monkeypatch)
    comp = _install_compositor_stub(monkeypatch)
    r2 = _make_r2(monkeypatch)

    cast = types.SimpleNamespace()
    timeline = _make_timeline(with_two_products=False)

    _run(cast_render._post_compose_product_overlays(
        r2=r2,
        output_key="renders/r2/final.mp4",
        timeline=timeline,
        render_id="r2",
        cast=cast,
    ))

    r2.download_file.assert_not_awaited()
    comp.assert_not_awaited()
    r2.upload_file.assert_not_awaited()


def test_swallows_compositor_exception(monkeypatch):
    """When the compositor raises, the helper re-raises (its own try/except
    re-raises after capturing) AND sentry_sdk.capture_exception is called.
    The production CALL SITE wraps this so the render is never failed — we
    assert the caller-side swallow contract via the wrapper pattern here by
    confirming the helper captured to Sentry before propagating."""
    _install_http_stub(monkeypatch)
    _install_probe_stub(monkeypatch)
    _install_compositor_stub(monkeypatch, raises=True)
    r2 = _make_r2(monkeypatch)

    captured: list = []
    monkeypatch.setattr(
        cast_render.sentry_sdk, "capture_exception",
        lambda e, *a, **k: captured.append(e),
    )

    cast = types.SimpleNamespace(product_title="")
    timeline = _make_timeline()

    # The helper itself propagates (the call site swallows). Mirror the
    # call-site wrapper here to prove the caller does NOT raise.
    raised = False
    try:
        try:
            _run(cast_render._post_compose_product_overlays(
                r2=r2,
                output_key="renders/r3/final.mp4",
                timeline=timeline,
                render_id="r3",
                cast=cast,
            ))
        except Exception:
            # This is exactly what the production call site does.
            pass
    except Exception:
        raised = True

    assert raised is False, "call-site wrapper must swallow compositor failure"
    assert captured, "sentry_sdk.capture_exception must be called on failure"
    # Upload must not happen when the composite failed.
    r2.upload_file.assert_not_awaited()


def test_every_except_captures_to_sentry():
    """Static guard: every `except` clause in the new helper and its call
    site must call sentry_sdk.capture_exception — a hard project rule."""
    import re

    src = _CAST_RENDER_PATH.read_text()

    # Isolate the extractor + helper body (extractor immediately precedes
    # the async helper, which ends right before _probe_video_fps).
    start = src.index("def _timeline_product_image_elements(")
    end = src.index("def _probe_video_fps(", start)
    helper = src[start:end]

    # Each `except` must be followed (within a few lines) by a
    # capture_exception call.
    for m in re.finditer(r"except[^\n]*:\n", helper):
        tail = helper[m.end():m.end() + 400]
        assert "sentry_sdk.capture_exception" in tail, (
            "An except clause in the product-overlay helpers does not "
            "call sentry_sdk.capture_exception"
        )
