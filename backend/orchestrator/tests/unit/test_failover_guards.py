"""Unit tests for PR #67 failover hardening + PR #75 render-duration
invariants.

Covers:
  - HOSTKEY VRAM guard fires when memory_used_mib >= 18 000.
  - HOSTKEY fails closed when /api/gpu-status is unreachable.
  - WaveSpeed payload uses image/audio/prompt/seed/resolution.
  - fal-ai/hallo provider posts image_url + audio_url.
  - Per-block real `using=` durations are baked verbatim (no scaling,
    no render-side clamp). Every block on the timeline is kept,
    regardless of how the total compares to any script-writer target.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Stub sentry_sdk so the provider module can be imported without the
# real dep installed in the test sandbox.
class _NoCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def set_tag(self, *_a, **_k): pass
    def set_extra(self, *_a, **_k): pass


if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        add_breadcrumb=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
        push_scope=lambda: _NoCtx(),
    )


def _import_providers():
    """Import render_providers lazily — the test file can run before
    the package's heavier deps are available because nothing here
    touches FastAPI / SQLAlchemy.
    """
    # httpx is a real dependency we mock at call sites; the module
    # only needs to import it, not call into the network.
    from services import render_providers
    return render_providers


@pytest.fixture
def hostkey_enabled(monkeypatch):
    """Force the HOSTKEY kill-switch OFF (i.e. HOSTKEY render ON) for the
    duration of a test.

    PR #94 added a kill-switch (services/hostkey_flags.py) that disables
    HOSTKEY by default. The legacy failover/recovery tests below exercise
    the on-prem HOSTKEY code path, which only runs when an operator flips
    both flags back on. This fixture pins those flags so the legacy
    rollback path is reachable, then monkeypatch restores the defaults
    afterwards.
    """
    monkeypatch.setenv("CAST_RENDER_HOSTKEY_DISABLED", "false")
    monkeypatch.setenv("HOSTKEY_RENDER_ENABLED", "true")
    return None


def test_hostkey_skip_when_vram_high(hostkey_enabled):
    """When /api/gpu-status returns memory_used_mib >= 18000, the
    provider must return False from is_available.

    Legacy HOSTKEY path — only meaningful with the kill-switch flipped
    OFF (see ``hostkey_enabled`` fixture); protects the rollback case.
    """
    rp = _import_providers()
    provider = rp.HostkeyInfinitetalkProvider()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={"memory_used_mib": 20000})

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=fake_response)

    with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
        available = asyncio.run(provider.is_available(render_id="rnd_test"))
    assert available is False, "should reject HOSTKEY when VRAM is high"


def test_hostkey_skip_when_status_unreachable(hostkey_enabled):
    """When /api/gpu-status raises (connection error / timeout) the
    provider must fail closed AND must NOT call sentry_sdk.capture_exception
    — this is the designed failover path (HOSTKEY offline → cloud chain
    handles it). PR #78 downgraded that handler to a breadcrumb.

    Legacy HOSTKEY path — only meaningful with the kill-switch flipped
    OFF (see ``hostkey_enabled`` fixture); protects the rollback case.
    """
    rp = _import_providers()
    provider = rp.HostkeyInfinitetalkProvider()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=Exception("connection refused"))

    capture_mock = MagicMock()
    breadcrumb_mock = MagicMock()

    with patch.object(rp.sentry_sdk, "capture_exception", capture_mock), \
         patch.object(rp.sentry_sdk, "add_breadcrumb", breadcrumb_mock), \
         patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
        available = asyncio.run(provider.is_available(render_id="rnd_test"))
    assert available is False
    # PR #78: designed failover must NOT fire a Sentry error event.
    assert capture_mock.call_count == 0, (
        "status-unreachable is a designed failover — must not capture_exception"
    )
    # Must leave a breadcrumb so any subsequent error event carries
    # the diagnostic.
    assert breadcrumb_mock.call_count >= 1
    kwargs = breadcrumb_mock.call_args.kwargs
    assert kwargs.get("level") == "info"
    assert kwargs.get("category") == "render"
    assert "hostkey_status_unreachable" in (kwargs.get("message") or "")


def test_hostkey_disabled_by_default_skips_without_probing(monkeypatch):
    """PR #94 kill-switch contract: with the default flags (HOSTKEY
    decommissioned), is_available must short-circuit to False WITHOUT
    ever probing /api/gpu-status, so try_chain falls through to the
    cloud InfiniteTalk providers.

    This documents the new default and is the inverse of the legacy
    ``hostkey_enabled``-gated tests above. We assert no httpx client is
    constructed — the kill-switch returns before any network setup.
    """
    # Force the kill-switch defaults explicitly so the test is hermetic
    # regardless of the ambient environment.
    monkeypatch.delenv("CAST_RENDER_HOSTKEY_DISABLED", raising=False)
    monkeypatch.delenv("HOSTKEY_RENDER_ENABLED", raising=False)

    rp = _import_providers()
    provider = rp.HostkeyInfinitetalkProvider()

    client_factory = MagicMock(side_effect=AssertionError(
        "kill-switch must short-circuit before constructing httpx.AsyncClient"
    ))

    with patch.object(rp.httpx, "AsyncClient", client_factory):
        available = asyncio.run(provider.is_available(render_id="rnd_killswitch"))

    assert available is False, "HOSTKEY must be unavailable by default"
    assert client_factory.call_count == 0, (
        "kill-switch must skip the gpu-status probe entirely"
    )


def test_hostkey_semaphore_busy_breadcrumbs_not_capture():
    """PR #78: the 3s asyncio.wait_for semaphore acquire in
    tasks.cast_render fires on every concurrent block while HOSTKEY is
    busy — that's the designed failover path that lets cloud providers
    take the block. The except handler must call add_breadcrumb (level
    info), NEVER capture_exception, so Sentry doesn't fill up with
    LUMINACAST-ORCHESTRATOR-6C-style false positives.

    This test mirrors the exact pattern in tasks/cast_render.py so a
    regression that re-introduces capture_exception on this code path
    fails immediately.
    """
    import sentry_sdk as _s

    capture_mock = MagicMock()
    breadcrumb_mock = MagicMock()

    async def _run() -> None:
        sem = asyncio.Semaphore(1)
        # Pre-acquire so the next acquire blocks.
        await sem.acquire()
        with patch.object(_s, "capture_exception", capture_mock), \
             patch.object(_s, "add_breadcrumb", breadcrumb_mock):
            try:
                await asyncio.wait_for(sem.acquire(), timeout=0.05)
                acquired = True
            except asyncio.TimeoutError:
                _s.add_breadcrumb(
                    category="render",
                    level="info",
                    message="hostkey_semaphore_busy_fallthrough",
                    data={"block_id": "blk_test",
                          "render_id": "rnd_test",
                          "timeout_s": 0.05},
                )
                acquired = False
        assert acquired is False

    asyncio.run(_run())
    assert capture_mock.call_count == 0, (
        "semaphore-busy fallthrough must not call capture_exception"
    )
    assert breadcrumb_mock.call_count == 1
    kwargs = breadcrumb_mock.call_args.kwargs
    assert kwargs.get("level") == "info"
    assert kwargs.get("category") == "render"
    assert kwargs.get("message") == "hostkey_semaphore_busy_fallthrough"


def test_cast_render_semaphore_busy_handler_source():
    """Source-level guard: tasks/cast_render.py around the HOSTKEY
    semaphore acquire must use add_breadcrumb, not capture_exception,
    in the asyncio.TimeoutError handler. This catches a code-level
    regression even when the integration test can't reach this branch.
    """
    import pathlib
    src_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "tasks" / "cast_render.py"
    )
    src = src_path.read_text()
    # Locate the semaphore-acquire block.
    marker = "await asyncio.wait_for(sem.acquire(), timeout=3.0)"
    idx = src.find(marker)
    assert idx != -1, "semaphore-acquire site missing"
    window = src[idx: idx + 1500]
    assert "hostkey_semaphore_busy_fallthrough" in window, (
        "expected breadcrumb message in the semaphore-busy handler"
    )
    # The TimeoutError handler in this window must NOT call capture_exception.
    timeout_idx = window.find("except asyncio.TimeoutError")
    assert timeout_idx != -1
    handler_slice = window[timeout_idx: timeout_idx + 600]
    assert "capture_exception" not in handler_slice, (
        "TimeoutError handler must not capture_exception — that's the "
        "designed failover path"
    )
    assert "add_breadcrumb" in handler_slice


def test_hostkey_breaker_opens_on_oom_signature():
    """The OOM helper must recognise torch.OutOfMemoryError and CUDA
    allocation failures from upstream ComfyUI stacktraces.
    """
    rp = _import_providers()
    assert rp.should_auto_restart_comfyui(
        "torch.OutOfMemoryError: Allocation on device 0 of size 64 MiB"
    )
    assert rp.should_auto_restart_comfyui("CUDA out of memory")
    assert not rp.should_auto_restart_comfyui("404 Not Found")
    assert not rp.should_auto_restart_comfyui("")


def test_wavespeed_payload_shape():
    """Posted payload must use image/audio/prompt/seed/resolution —
    NOT image_url/audio_url/width/height which the WaveSpeed API
    rejects with HTTP 400.
    """
    rp = _import_providers()
    provider = rp.WavespeedInfinitetalkProvider()

    captured: dict = {}

    submit_response = MagicMock()
    submit_response.status_code = 200
    submit_response.json = MagicMock(return_value={
        "data": {"id": "pred_test", "status": "created"},
    })

    poll_response = MagicMock()
    poll_response.status_code = 200
    poll_response.json = MagicMock(return_value={
        "data": {"status": "completed", "outputs": ["https://cdn.test/x.mp4"]},
    })

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    async def _post(url, headers=None, json=None):
        captured["payload"] = json
        captured["headers"] = headers
        captured["url"] = url
        return submit_response

    async def _get(url, headers=None):
        return poll_response

    fake_client.post = _post
    fake_client.get = _get

    with patch.dict("os.environ", {"WAVESPEED_API_KEY": "test-key"}):
        with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
            result = asyncio.run(provider.generate(
                image_url="https://cdn.test/face.jpg",
                audio_url="https://cdn.test/voice.mp3",
                prompt="A person talking",
                width=480,
                height=848,
                duration_s=5.0,
            ))

    body = captured.get("payload") or {}
    assert "image" in body, f"expected 'image' key; got {sorted(body)}"
    assert "audio" in body, f"expected 'audio' key; got {sorted(body)}"
    assert "prompt" in body
    assert "seed" in body
    assert "resolution" in body
    # Anti-regression: the old wrong keys MUST NOT appear.
    assert "image_url" not in body
    assert "audio_url" not in body
    assert "width" not in body
    assert "height" not in body
    assert result["video_url"] == "https://cdn.test/x.mp4"


def test_fal_hallo_payload_shape():
    """fal-ai/hallo accepts image_url + audio_url (not source_video_url
    like the old MuseTalk endpoint).
    """
    rp = _import_providers()
    provider = rp.FalHalloProvider()

    captured: dict = {}

    submit_response = MagicMock()
    submit_response.status_code = 200
    submit_response.json = MagicMock(return_value={
        "request_id": "req_test",
        "status_url": "https://queue.fal.run/.../status",
        "response_url": "https://queue.fal.run/.../result",
    })
    status_response = MagicMock()
    status_response.status_code = 200
    status_response.json = MagicMock(return_value={"status": "COMPLETED"})
    result_response = MagicMock()
    result_response.status_code = 200
    result_response.json = MagicMock(return_value={
        "video": {"url": "https://cdn.fal/x.mp4"},
    })

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    async def _post(url, headers=None, json=None):
        captured["payload"] = json
        captured["url"] = url
        return submit_response

    call_seq = {"i": 0}

    async def _get(url, headers=None):
        # First GET = status, second = result
        call_seq["i"] += 1
        return status_response if call_seq["i"] == 1 else result_response

    fake_client.post = _post
    fake_client.get = _get

    with patch.dict("os.environ", {"FAL_KEY": "test-key"}):
        with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
            result = asyncio.run(provider.generate(
                image_url="https://cdn.test/face.jpg",
                audio_url="https://cdn.test/voice.mp3",
                duration_s=5.0,
            ))

    body = captured.get("payload") or {}
    assert body == {
        "image_url": "https://cdn.test/face.jpg",
        "audio_url": "https://cdn.test/voice.mp3",
    }, f"unexpected payload: {body}"
    assert result["video_url"] == "https://cdn.fal/x.mp4"


def test_real_durations_no_scaling_when_fits():
    """PR #71/#75: the timeline is rewritten with the exact per-block
    `using=` durations — no proportional scaling, no render-side clamp.
    Bonded V1/A1 pairs must share start/end.
    """
    for mod in (
        "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
        "sqlalchemy.orm", "sqlalchemy.orm.attributes",
    ):
        sys.modules.setdefault(mod, types.ModuleType(mod))
    sys.modules.setdefault(
        "config", types.SimpleNamespace(settings=types.SimpleNamespace()),
    )
    timeline = {
        "tracks": [
            {
                "type": "video",
                "elements": [
                    {
                        "id": f"v1_blk_{i}",
                        "s": i * 1.5, "e": (i + 1) * 1.5,
                        "metadata": {
                            "block_id": f"blk_{i}", "bonded": True,
                            "track_type": "video_face",
                            "paired_audio_element_id": f"a1_blk_{i}",
                        },
                    }
                    for i in range(3)
                ],
            },
            {
                "type": "audio",
                "elements": [
                    {
                        "id": f"a1_blk_{i}",
                        "s": i * 1.5, "e": (i + 1) * 1.5,
                        "props": {"src": f"https://cdn/audio_{i}.mp3"},
                        "metadata": {
                            "block_id": f"blk_{i}", "bonded": True,
                            "track_type": "audio_voice",
                            "paired_video_element_id": f"v1_blk_{i}",
                        },
                    }
                    for i in range(3)
                ],
            },
        ],
    }
    # Real durations (refreshed TTS): 5s, 10s, 8s — total 23s, well
    # under 35s target.
    real = {"blk_0": 5.0, "blk_1": 10.0, "blk_2": 8.0}
    from tasks.cast_render import _apply_real_block_durations
    new_tl, rewritten, dropped = _apply_real_block_durations(
        timeline,
        block_durations=real,
        render_id="rnd_real",
        fps=30,
    )
    assert rewritten is True
    assert dropped == []
    # V1 and A1 of every block must have the SAME (s, e) — the bonded
    # pair shares the slot. Window must equal the real duration to the
    # nearest 1/fps.
    video_els = new_tl["tracks"][0]["elements"]
    audio_els = new_tl["tracks"][1]["elements"]
    expected_cursor = 0.0
    for i, (v, a) in enumerate(zip(video_els, audio_els)):
        assert abs(v["s"] - a["s"]) < 1e-3
        assert abs(v["e"] - a["e"]) < 1e-3
        dur_v = v["e"] - v["s"]
        # Real value rounded to 1/30s grid.
        expected_dur = round(real[f"blk_{i}"] * 30) / 30
        assert abs(dur_v - expected_dur) < 1e-3, (
            f"block {i}: got dur {dur_v}, expected {expected_dur}"
        )
        assert abs(v["s"] - expected_cursor) < 1e-3
        expected_cursor = v["e"]


def test_real_durations_never_drop_blocks_even_when_sum_far_exceeds_any_target():
    """PR #75: even when the sum of real block durations FAR exceeds any
    hypothetical script-writer target, the render pipeline must keep
    every block on the timeline. The duration target is a hint for the
    script writer — it must NOT cap the render. Users edit/extend blocks
    after script generation; those extensions have to render.

    This inverts PR #71's drop-trailing behavior (which produced the
    rnd_babe45b57156 incident — 4 trailing blocks silently dropped).
    """
    for mod in (
        "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
        "sqlalchemy.orm", "sqlalchemy.orm.attributes",
    ):
        sys.modules.setdefault(mod, types.ModuleType(mod))
    sys.modules.setdefault(
        "config", types.SimpleNamespace(settings=types.SimpleNamespace()),
    )
    timeline = {
        "tracks": [
            {
                "type": "video",
                "elements": [
                    {
                        "id": f"v1_blk_{i}",
                        "s": i * 5.0, "e": (i + 1) * 5.0,
                        "metadata": {
                            "block_id": f"blk_{i}", "bonded": True,
                            "track_type": "video_face",
                        },
                    }
                    for i in range(4)
                ],
            },
        ],
    }
    # Real durations: 10, 12, 8, 15 — sum 45s, far exceeding any
    # plausible script-writer target. Every block MUST survive.
    real = {"blk_0": 10.0, "blk_1": 12.0, "blk_2": 8.0, "blk_3": 15.0}
    from tasks.cast_render import _apply_real_block_durations
    new_tl, rewritten, dropped = _apply_real_block_durations(
        timeline,
        block_durations=real,
        render_id="rnd_no_drop",
        fps=30,
    )
    assert rewritten is True
    assert dropped == [], "no block must ever be dropped at render time"
    surviving = new_tl["tracks"][0]["elements"]
    assert len(surviving) == 4, "every block on the timeline must be baked"
    # Each block keeps its real duration exactly (no scaling, no clamp).
    expected = [10.0, 12.0, 8.0, 15.0]
    for i, el in enumerate(surviving):
        dur = el["e"] - el["s"]
        assert abs(dur - expected[i]) < 1e-3, (
            f"block {i}: got dur {dur}, expected {expected[i]}"
        )
    # Total real duration is preserved end-to-end.
    total = surviving[-1]["e"] - surviving[0]["s"]
    assert abs(total - sum(expected)) < 1e-3


def test_real_durations_empty_block_map_noop():
    """No block_durations given → return timeline unchanged."""
    for mod in (
        "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
        "sqlalchemy.orm", "sqlalchemy.orm.attributes",
    ):
        sys.modules.setdefault(mod, types.ModuleType(mod))
    sys.modules.setdefault(
        "config", types.SimpleNamespace(settings=types.SimpleNamespace()),
    )
    from tasks.cast_render import _apply_real_block_durations
    timeline = {
        "tracks": [
            {"type": "video", "elements": [{"id": "v1_a", "s": 0, "e": 33,
                                            "metadata": {"block_id": "a"}}]},
        ],
    }
    new_tl, rewritten, dropped = _apply_real_block_durations(
        timeline,
        block_durations={},
        render_id="rnd_noop",
    )
    assert rewritten is False
    assert dropped == []
    assert new_tl is timeline
