"""Unit tests for Render_Quality_Duration_Validation.md §3.2 — the motion
duration gate in ``services.media_processing.validate_baked_clip``.

Synthesizes short clips via ffmpeg lavfi; skipped when ffmpeg/ffprobe are
absent. ``sentry_sdk`` is a real installed dependency — do NOT stub it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

from services.media_processing import (
    ClipValidationReason,
    validate_baked_clip,
)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; motion-gate synth tests need both",
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _synth_clip(path: str, *, duration_s: float, with_audio: bool = True) -> None:
    # Moving test pattern so the freeze detector never trips on these clips.
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-f", "lavfi", "-i",
        f"testsrc2=size=320x568:duration={duration_s}:rate=30",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}"]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    ]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2"]
    cmd += ["-t", f"{duration_s}", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")


def test_motion_clip_too_short_for_slot():
    # rnd_7923dde6c01f: 1.07s motion bake for a 3.63s slot. Below the 2.0s
    # motion structural minimum → motion_clip_too_short (provider junk).
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        _synth_clip(clip, duration_s=1.07)
        reason = _run(
            validate_baked_clip(
                clip, block_id="blk", render_id="r",
                require_audio=False, is_motion=True, slot_duration_s=3.63,
            )
        )
        assert reason == ClipValidationReason.MOTION_CLIP_TOO_SHORT


def test_motion_clip_undershoot_near_miss():
    # 4.8s motion clip for a 5.0s slot (4% short) is above the 2.0s structural
    # minimum but below the slot floor (5.0 * 0.99 = 4.95) → duration_undershoot.
    # (Here the post-1.3 micro-slowdown did NOT lift it to the floor.)
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        _synth_clip(clip, duration_s=4.8)
        reason = _run(
            validate_baked_clip(
                clip, block_id="blk", render_id="r",
                require_audio=False, is_motion=True, slot_duration_s=5.0,
            )
        )
        assert reason == ClipValidationReason.DURATION_UNDERSHOOT


def test_motion_clip_over_slot_accepted():
    # 5.1s motion clip for a 5.0s slot — over the slot is fine; the composer
    # head-trims the surplus.
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        _synth_clip(clip, duration_s=5.1)
        reason = _run(
            validate_baked_clip(
                clip, block_id="blk", render_id="r",
                require_audio=False, is_motion=True, slot_duration_s=5.0,
            )
        )
        assert reason == ClipValidationReason.OK


def test_motion_clip_at_slot_within_tolerance_accepted():
    # Exactly at slot (within the 1% rounding floor) → accepted.
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        _synth_clip(clip, duration_s=5.0)
        reason = _run(
            validate_baked_clip(
                clip, block_id="blk", render_id="r",
                require_audio=False, is_motion=True, slot_duration_s=5.0,
            )
        )
        assert reason == ClipValidationReason.OK


def test_motion_clip_short_slot_two_frame_undershoot_accepted():
    # rnd_5404e2add641 / blk_acab62acb226: a 2.5s slot is short enough that
    # the 1% percentage tolerance (2.475s) is TIGHTER than the 2-frame
    # rounding guard (2.5 - 2/30 = 2.433s) — exactly the case the floor is
    # supposed to fall back to the looser bound for. A clip landing 1 frame
    # short (2.467s, from a real head-trim + topup rounding chain) must be
    # accepted, not rejected — the bug (floor = max(...) instead of min(...))
    # picked the stricter percentage floor here and rejected it.
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        _synth_clip(clip, duration_s=2.467)
        reason = _run(
            validate_baked_clip(
                clip, block_id="blk", render_id="r",
                require_audio=False, is_motion=True, slot_duration_s=2.5,
            )
        )
        assert reason == ClipValidationReason.OK


def test_speaking_clip_not_subject_to_motion_gate():
    # A 1.07s SPEAKING clip is NOT failed by the motion gate (is_motion=False);
    # it clears the lenient 0.5s structural floor. Its own audio-relative
    # tolerance (Phase 2) is enforced elsewhere, not here.
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        _synth_clip(clip, duration_s=1.07)
        reason = _run(
            validate_baked_clip(
                clip, block_id="blk", render_id="r",
                require_audio=False, is_motion=False, slot_duration_s=3.63,
            )
        )
        assert reason == ClipValidationReason.OK
