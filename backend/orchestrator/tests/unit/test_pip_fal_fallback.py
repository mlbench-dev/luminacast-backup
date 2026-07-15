"""Unit tests for the PIP fal.ai sync-lipsync fallback tier.

Context: PIP (talking-head) blocks have only a face image + audio. With
HOSTKEY decommissioned (MuseTalk gone) and the RunPod InfiniteTalk template
`triazevwb6a8ap` failing fast with "Video not found" since early May 2026,
a PIP block used to hard-fail. The dispatcher now inserts a fal.ai
sync-lipsync v2/pro tier (driven off a looped face still) BEFORE the
known-broken RunPod tier.

These tests confirm the dispatch ORDER: with HOSTKEY unavailable and Modal
unconfigured, a PIP block must hit fal.ai lipsync first, and only fall back
to RunPod if fal.ai itself raises.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import patch

import pytest


# ─── Stub sentry_sdk so importing the module doesn't pull in the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
        capture_message=lambda *_a, **_k: None,
        push_scope=lambda *_a, **_k: _NullScope(),
    )


class _NullScope:
    def __enter__(self):
        return types.SimpleNamespace(
            set_tag=lambda *_a, **_k: None,
            set_extra=lambda *_a, **_k: None,
        )

    def __exit__(self, *_a):
        return False


def _import_dispatcher():
    from services import render_dispatcher
    return render_dispatcher


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_pip_dispatcher(rd):
    """A RenderDispatcher with HOSTKEY forced unavailable so the PIP path
    falls through to the cloud tiers deterministically."""
    dispatcher = rd.RenderDispatcher()

    async def _no_hostkey():
        return False

    dispatcher._hostkey_available = _no_hostkey  # type: ignore[assignment]
    return dispatcher


def test_pip_dispatches_to_fal_lipsync_before_runpod():
    """HOSTKEY down + Modal unconfigured + MuseTalk disabled → a PIP block
    must be served by fal.ai sync-lipsync, NOT RunPod."""
    rd = _import_dispatcher()
    calls: list[str] = []

    async def _fake_fal(**kwargs):
        calls.append("fal")
        return {
            "output": {"video_url": "https://fal.test/lipsync.mp4"},
            "backend": "fal_sync_lipsync_v2_pro",
        }

    async def _fake_runpod(*_a, **_k):
        calls.append("runpod")
        return {"output": {"video_url": "https://runpod.test/x.mp4"}, "backend": "runpod"}

    dispatcher = _make_pip_dispatcher(rd)

    with patch.object(rd, "MODAL_ENDPOINT_URL", ""), \
         patch.object(rd, "MUSETALK_ENABLED", False), \
         patch.object(rd, "HOSTKEY_RENDER_ENABLED", False), \
         patch(
             "services.render_providers.pip_lipsync_via_fal",
             side_effect=_fake_fal,
         ), \
         patch.object(dispatcher, "_render_on_runpod", side_effect=_fake_runpod):
        result = _run(dispatcher.submit_and_wait(
            image_url="https://media.test/face_ref.jpg",
            audio_url="https://media.test/a.wav",
            prompt="talking head",
            size="480p",
            audio_duration_s=4.57,
            is_pip=True,
            block_id="blk_c59218e232f4",
        ))

    assert calls == ["fal"], f"expected fal only, got {calls}"
    assert result["backend"] == "fal_sync_lipsync_v2_pro"
    assert result["output"]["video_url"] == "https://fal.test/lipsync.mp4"


def test_pip_falls_back_to_runpod_only_after_fal_fails():
    """If fal.ai lipsync raises, the dispatcher must fall back to RunPod —
    and fal MUST be attempted strictly before RunPod."""
    rd = _import_dispatcher()
    calls: list[str] = []

    async def _fail_fal(**kwargs):
        calls.append("fal")
        raise RuntimeError("fal exploded")

    async def _fake_runpod(*_a, **_k):
        calls.append("runpod")
        return {"output": {"video_url": "https://runpod.test/x.mp4"}, "backend": "runpod"}

    dispatcher = _make_pip_dispatcher(rd)

    with patch.object(rd, "MODAL_ENDPOINT_URL", ""), \
         patch.object(rd, "MUSETALK_ENABLED", False), \
         patch.object(rd, "HOSTKEY_RENDER_ENABLED", False), \
         patch(
             "services.render_providers.pip_lipsync_via_fal",
             side_effect=_fail_fal,
         ), \
         patch.object(dispatcher, "_render_on_runpod", side_effect=_fake_runpod):
        result = _run(dispatcher.submit_and_wait(
            image_url="https://media.test/face_ref.jpg",
            audio_url="https://media.test/a.wav",
            prompt="talking head",
            size="480p",
            audio_duration_s=4.57,
            is_pip=True,
            block_id="blk_c59218e232f4",
        ))

    assert calls == ["fal", "runpod"], f"fal must precede runpod, got {calls}"
    assert result["backend"] == "runpod"


def test_non_pip_dispatches_to_fal_lipsync_before_runpod():
    """PR-I: non-PIP speaking blocks ALSO have only image + audio + duration.
    With HOSTKEY disabled, MODAL_ENDPOINT_URL empty, and is_pip=False, the
    dispatcher must call pip_lipsync_via_fal (Tier 3) BEFORE reaching the
    broken RunPod tier — previously this tier was gated on is_pip and a
    non-PIP block fell straight through to RunPod with no working renderer."""
    rd = _import_dispatcher()
    calls: list[str] = []

    async def _fake_fal(**kwargs):
        calls.append("fal")
        return {
            "output": {"video_url": "https://fal.test/lipsync.mp4"},
            "backend": "fal_sync_lipsync_v2_pro",
        }

    async def _fake_runpod(*_a, **_k):
        calls.append("runpod")
        return {"output": {"video_url": "https://runpod.test/x.mp4"}, "backend": "runpod"}

    dispatcher = _make_pip_dispatcher(rd)

    with patch.object(rd, "MODAL_ENDPOINT_URL", ""), \
         patch.object(rd, "MUSETALK_ENABLED", False), \
         patch.object(rd, "HOSTKEY_RENDER_ENABLED", False), \
         patch(
             "services.render_providers.pip_lipsync_via_fal",
             side_effect=_fake_fal,
         ), \
         patch.object(dispatcher, "_render_on_runpod", side_effect=_fake_runpod):
        result = _run(dispatcher.submit_and_wait(
            image_url="https://media.test/speaker.jpg",
            audio_url="https://media.test/a.wav",
            prompt="full-shot speaking block",
            size="480p",
            audio_duration_s=6.12,
            is_pip=False,
            block_id="blk_nonpip_77a1",
        ))

    assert calls == ["fal"], f"expected fal only (before runpod), got {calls}"
    assert result["backend"] == "fal_sync_lipsync_v2_pro"
    assert result["output"]["video_url"] == "https://fal.test/lipsync.mp4"


def test_non_pip_falls_back_to_runpod_only_after_fal_fails():
    """For non-PIP blocks too, RunPod stays the last-resort fallback: it is
    reached only after fal.ai sync-lipsync raises, never before."""
    rd = _import_dispatcher()
    calls: list[str] = []

    async def _fail_fal(**kwargs):
        calls.append("fal")
        raise RuntimeError("fal exploded")

    async def _fake_runpod(*_a, **_k):
        calls.append("runpod")
        return {"output": {"video_url": "https://runpod.test/x.mp4"}, "backend": "runpod"}

    dispatcher = _make_pip_dispatcher(rd)

    with patch.object(rd, "MODAL_ENDPOINT_URL", ""), \
         patch.object(rd, "MUSETALK_ENABLED", False), \
         patch.object(rd, "HOSTKEY_RENDER_ENABLED", False), \
         patch(
             "services.render_providers.pip_lipsync_via_fal",
             side_effect=_fail_fal,
         ), \
         patch.object(dispatcher, "_render_on_runpod", side_effect=_fake_runpod):
        result = _run(dispatcher.submit_and_wait(
            image_url="https://media.test/speaker.jpg",
            audio_url="https://media.test/a.wav",
            prompt="full-shot speaking block",
            size="480p",
            audio_duration_s=6.12,
            is_pip=False,
            block_id="blk_nonpip_77a1",
        ))

    assert calls == ["fal", "runpod"], f"fal must precede runpod, got {calls}"
    assert result["backend"] == "runpod"


def test_pip_lipsync_via_fal_loops_face_then_calls_v2_pro():
    """The helper must loop the face image into a still video and drive
    fal sync-lipsync v2/pro off of it with sync_mode=loop."""
    from services import render_providers as rp
    captured: dict = {}

    async def _fake_loop(face_ref_url, *, block_id, duration_s, width, height, fps=25):
        captured["loop_args"] = {
            "face_ref_url": face_ref_url,
            "block_id": block_id,
            "duration_s": duration_s,
            "width": width,
            "height": height,
        }
        return "https://r2.test/tmp/pip_face_loops/blk.mp4?sig=x"

    async def _fake_generate(self, *, video_url=None, audio_url=None,
                             duration_s=0.0, audio_duration_s=None,
                             sync_mode=None, **_kw):
        captured["generate"] = {
            "video_url": video_url,
            "audio_url": audio_url,
            "sync_mode": sync_mode,
        }
        return {"video_url": "https://fal.test/out.mp4", "duration_s": duration_s}

    with patch.object(rp, "_loop_face_to_still_video", side_effect=_fake_loop), \
         patch.object(rp.FalSyncLipsyncV2ProProvider, "generate", _fake_generate):
        result = _run(rp.pip_lipsync_via_fal(
            face_ref_url="https://media.test/face_ref.jpg",
            audio_url="https://media.test/a.wav",
            block_id="blk_c59218e232f4",
            duration_s=4.57,
            width=480,
            height=848,
        ))

    assert captured["loop_args"]["face_ref_url"] == "https://media.test/face_ref.jpg"
    assert captured["loop_args"]["block_id"] == "blk_c59218e232f4"
    # The driving video for fal is the looped still, NOT the raw face image.
    assert captured["generate"]["video_url"] == "https://r2.test/tmp/pip_face_loops/blk.mp4?sig=x"
    assert captured["generate"]["sync_mode"] == "loop"
    assert result["backend"] == "fal_sync_lipsync_v2_pro"
    assert result["output"]["video_url"] == "https://fal.test/out.mp4"
