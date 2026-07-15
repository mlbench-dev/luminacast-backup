"""Unit tests for the in-process ffmpeg-compose fallback in
`backend/orchestrator/tasks/cast_render.py` (final compose dispatch, ~L4406).

Background: the full-timeline compose used to run on the decommissioned
HOSTKEY box via an HTTP POST to ``GPU_WORKER_URL`` / ``HOSTKEY_GPU_URL``.
After HOSTKEY was killed (PR #94) every render failed at compose because
no cloud worker URL is configured. The fix routes the dispatch three ways:

  1. ``GPU_WORKER_URL`` set        -> HTTP POST to that worker.
  2. HOSTKEY disabled, no URL      -> run ``_run_ffmpeg_compose`` in-process
                                      via ``asyncio.to_thread`` (no HTTP).
  3. HOSTKEY enabled, no URL       -> legacy ``HOSTKEY_GPU_URL`` HTTP POST.

The production branch lives deep inside ``_render_async`` and isn't directly
callable without a full DB / R2 mock harness, so — mirroring the existing
``test_cast_render_dispatch.py`` — we reproduce the patched routing verbatim
in an isolated coroutine and assert the contract, then add static guards so a
future revert is caught by CI.
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Stub sentry_sdk so importing the modules doesn't pull the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

import sentry_sdk  # noqa: E402  (uses the stub above when real dep absent)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_COMPOSITION_PAYLOAD = {
    "render_id": "rnd_test",
    "cast_id": "cst_test",
    "timeline": {"tracks": []},
    "baked_urls": {},
    "overlay_elements": [],
    "compose_video_tracks": [],
    "compose_audio_tracks": [],
}


async def _patched_compose_dispatch(
    *,
    cloud_worker_url,
    hostkey_disabled,
    hostkey_gpu_url,
    run_in_process,
    http_client_factory,
):
    """Reproduce the patched compose-dispatch routing verbatim.

    ``run_in_process`` stands in for ``_run_ffmpeg_compose`` and
    ``http_client_factory`` for ``httpx.AsyncClient``. Returns the
    ``compose_result`` dict the production code would carry forward.
    """
    composition_payload = dict(_COMPOSITION_PAYLOAD)

    if cloud_worker_url:
        gpu_url = cloud_worker_url
        async with http_client_factory(timeout=600.0) as http:
            resp = await http.post(
                f"{gpu_url.rstrip('/')}/api/ffmpeg-compose",
                json=composition_payload,
            )
            resp.raise_for_status()
            compose_result = resp.json()
    elif hostkey_disabled:
        compose_req = types.SimpleNamespace(**composition_payload)
        try:
            compose_result = await asyncio.to_thread(run_in_process, compose_req)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise
    else:
        gpu_url = hostkey_gpu_url
        if not gpu_url:
            raise RuntimeError("GPU_WORKER_URL / HOSTKEY_GPU_URL not configured")
        async with http_client_factory(timeout=600.0) as http:
            resp = await http.post(
                f"{gpu_url.rstrip('/')}/api/ffmpeg-compose",
                json=composition_payload,
            )
            resp.raise_for_status()
            compose_result = resp.json()

    return compose_result


def _make_http_factory():
    """Build a MagicMock that mimics ``httpx.AsyncClient(...)`` used as an
    async context manager, returning a response with ``output_r2_key``.
    """
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"output_r2_key": "renders/rnd_test/final.mp4"})

    client = MagicMock()
    client.post = AsyncMock(return_value=resp)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=ctx)
    return factory, client


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — GPU_WORKER_URL empty + HOSTKEY disabled -> in-process, no HTTP
# ─────────────────────────────────────────────────────────────────────────────

def test_in_process_when_no_url_and_hostkey_disabled():
    run_in_process = MagicMock(
        return_value={"output_r2_key": "renders/rnd_test/final.mp4"}
    )
    http_factory, http_client = _make_http_factory()

    result = _run(
        _patched_compose_dispatch(
            cloud_worker_url="",
            hostkey_disabled=True,
            hostkey_gpu_url="",
            run_in_process=run_in_process,
            http_client_factory=http_factory,
        )
    )

    assert run_in_process.call_count == 1, (
        "In-process compose must be called when no GPU_WORKER_URL is set and "
        "HOSTKEY is disabled."
    )
    # The payload must be passed as an attribute-accessible object.
    (compose_req,), _ = run_in_process.call_args
    assert compose_req.render_id == "rnd_test"
    assert compose_req.cast_id == "cst_test"
    # HTTP path must NOT be taken.
    assert http_factory.call_count == 0
    assert http_client.post.call_count == 0
    assert result["output_r2_key"] == "renders/rnd_test/final.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — GPU_WORKER_URL set -> HTTP path, in-process NOT called
# ─────────────────────────────────────────────────────────────────────────────

def test_http_when_gpu_worker_url_set():
    run_in_process = MagicMock(
        return_value={"output_r2_key": "should-not-be-used"}
    )
    http_factory, http_client = _make_http_factory()

    result = _run(
        _patched_compose_dispatch(
            cloud_worker_url="https://compose.example.com",
            hostkey_disabled=True,  # even disabled, an explicit URL wins
            hostkey_gpu_url="",
            run_in_process=run_in_process,
            http_client_factory=http_factory,
        )
    )

    assert run_in_process.call_count == 0, (
        "In-process compose must NOT run when GPU_WORKER_URL is configured."
    )
    assert http_client.post.call_count == 1
    (posted_url,), kwargs = http_client.post.call_args
    assert posted_url == "https://compose.example.com/api/ffmpeg-compose"
    assert kwargs["json"]["render_id"] == "rnd_test"
    assert result["output_r2_key"] == "renders/rnd_test/final.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — GPU_WORKER_URL empty + HOSTKEY enabled -> HOSTKEY_GPU_URL HTTP POST
# ─────────────────────────────────────────────────────────────────────────────

def test_hostkey_url_when_enabled_and_no_gpu_worker_url():
    run_in_process = MagicMock()
    http_factory, http_client = _make_http_factory()

    result = _run(
        _patched_compose_dispatch(
            cloud_worker_url="",
            hostkey_disabled=False,  # HOSTKEY explicitly re-enabled
            hostkey_gpu_url="http://194.247.183.12:8000",
            run_in_process=run_in_process,
            http_client_factory=http_factory,
        )
    )

    assert run_in_process.call_count == 0
    assert http_client.post.call_count == 1
    (posted_url,), _ = http_client.post.call_args
    assert posted_url == "http://194.247.183.12:8000/api/ffmpeg-compose"
    assert result["output_r2_key"] == "renders/rnd_test/final.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — in-process failure is captured to Sentry and re-raised
# ─────────────────────────────────────────────────────────────────────────────

def test_in_process_failure_captured_and_reraised():
    boom = RuntimeError("ffmpeg blew up")
    run_in_process = MagicMock(side_effect=boom)
    http_factory, _ = _make_http_factory()

    with patch.object(sentry_sdk, "capture_exception") as cap:
        with pytest.raises(RuntimeError, match="ffmpeg blew up"):
            _run(
                _patched_compose_dispatch(
                    cloud_worker_url="",
                    hostkey_disabled=True,
                    hostkey_gpu_url="",
                    run_in_process=run_in_process,
                    http_client_factory=http_factory,
                )
            )
        assert cap.call_count == 1, (
            "In-process compose failures must be reported to Sentry before "
            "re-raising (project standard)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Static guards — make sure the production file actually has the routing
# ─────────────────────────────────────────────────────────────────────────────

_THIS_DIR = Path(__file__).resolve().parent
_CAST_RENDER = _THIS_DIR.parent.parent / "tasks" / "cast_render.py"
_COMPOSE_MODULE = _THIS_DIR.parent.parent / "worker_ffmpeg_compose.py"


def test_cast_render_has_in_process_path():
    """Guard: cast_render.py must run compose in-process via
    asyncio.to_thread(_run_ffmpeg_compose, ...) on the no-URL/HOSTKEY-disabled
    branch — not raise ConfigurationError as it did before the fix.
    """
    src = _CAST_RENDER.read_text()
    assert "from worker_ffmpeg_compose import _run_ffmpeg_compose" in src, (
        "Expected in-process import of _run_ffmpeg_compose — fix missing."
    )
    assert re.search(
        r"asyncio\.to_thread\(\s*_run_ffmpeg_compose", src
    ), "Expected asyncio.to_thread(_run_ffmpeg_compose, ...) — fix missing."
    assert (
        "GPU_WORKER_URL is required when HOSTKEY kill-switch is enabled"
        not in src
    ), "The old hard-fail ConfigurationError must be gone."
    assert (
        "Running FFmpeg composition in-process (no GPU_WORKER_URL" in src
    ), "Expected the in-process log line — fix missing."


def test_in_process_compose_module_importable():
    """Guard: the compose module must be importable from the orchestrator
    package root so the celery render worker (PYTHONPATH=/app, the COPY . .
    target) can load it.
    """
    assert _COMPOSE_MODULE.exists(), (
        "worker_ffmpeg_compose.py must live at the orchestrator package root "
        "so it lands in the image and is importable in-process."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
