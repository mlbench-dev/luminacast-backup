"""Unit tests for services.media_processing.validate_baked_clip (Phase 3).

The validator runs five checks on a baked clip and returns the first
failing ClipValidationReason (or OK). Inputs are synthesized via ffmpeg
lavfi sources (color, testsrc2, sine, anullsrc), so no GPU / network is
needed; skipped when ffmpeg/ffprobe are absent.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

from services.media_processing import (
    validate_baked_clip,
    ClipValidationReason,
    _sum_detect_intervals,
    _trailing_detect_start,
)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; validate_baked_clip tests need both",
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", *args], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")


def _good_clip(path: str, *, duration_s: float = 3.0) -> None:
    """Moving testsrc2 video + tone audio — passes every check."""
    _ffmpeg([
        "-f", "lavfi", "-i", f"testsrc2=size=480x848:duration={duration_s}:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration_s}", path,
    ])


def test_good_clip_passes():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "good.mp4")
        _good_clip(clip, duration_s=3.0)
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.OK


def test_all_black_clip_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "black.mp4")
        # Pure black video + audio so only the black check trips.
        _ffmpeg([
            "-f", "lavfi", "-i", "color=c=black:size=480x848:duration=3:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-t", "3", clip,
        ])
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.MOSTLY_BLACK


def test_frozen_clip_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "frozen.mp4")
        # A single still color frame held for the whole duration → frozen.
        # Use a non-black color so the black check doesn't trip first.
        _ffmpeg([
            "-f", "lavfi", "-i", "color=c=blue:size=480x848:duration=3:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-t", "3", clip,
        ])
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.MOSTLY_FROZEN


def test_too_short_clip_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "short.mp4")
        _good_clip(clip, duration_s=0.2)  # below the 0.5s floor
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.TOO_SHORT


def test_missing_audio_rejected_when_required():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "noaudio.mp4")
        _ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=480x848:duration=3:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", "-t", "3", clip,
        ])
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.NO_AUDIO


def test_missing_audio_ok_when_not_required():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "noaudio.mp4")
        _ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=480x848:duration=3:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", "-t", "3", clip,
        ])
        reason = _run(
            validate_baked_clip(clip, block_id="b", render_id="r", require_audio=False)
        )
        assert reason == ClipValidationReason.OK


def test_structural_failure_on_non_video():
    with tempfile.TemporaryDirectory() as tmp:
        bogus = os.path.join(tmp, "bogus.mp4")
        with open(bogus, "wb") as f:
            f.write(b"not a real mp4 file")
        reason = _run(validate_baked_clip(bogus, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.STRUCTURAL


def test_sum_detect_intervals_parses_black_and_freeze():
    sample = (
        "[blackdetect @ 0x1] black_start:0 black_end:1.5 black_duration:1.5\n"
        "[blackdetect @ 0x1] black_start:2 black_end:2.8 black_duration:0.8\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 1.733\n"
        "some unrelated line\n"
    )
    assert abs(_sum_detect_intervals(sample, "black") - 2.3) < 1e-6
    assert abs(_sum_detect_intervals(sample, "freeze") - 1.733) < 1e-6


def _head_motion_tail_freeze_clip(
    path: str, *, head_s: float, tail_s: float, fps: int = 30
) -> None:
    """Concat a moving-head segment with a held-last-frame tail.

    Produces exactly the audio-under-slot extension shape: ``head_s`` of full
    motion followed by ``tail_s`` of a frozen last frame, audio across the
    whole clip. tpad=stop_mode=clone holds the final frame of the motion head.
    """
    total = head_s + tail_s
    vf = (
        f"testsrc2=size=480x848:duration={head_s}:rate={fps},"
        f"tpad=stop_mode=clone:stop_duration={tail_s}"
    )
    _ffmpeg([
        "-f", "lavfi", "-i", vf,
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={total}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-t", f"{total}", path,
    ])


def test_tail_freeze_extension_passes_validator():
    """Head motion + short frozen tail (audio-under-slot extension) → OK.

    A clip whose head plays with motion and whose only freeze is a trailing
    hold ≤ tail tolerance must PASS, even though the frozen tail's fraction of
    the total duration exceeds the 70% freeze threshold. This is the
    legitimate output of extending a short TTS clip to fill its user slot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "tail_freeze.mp4")
        # 2.0s motion head + 1.2s frozen tail (tail ≤ 1.5s tolerance). The
        # frozen tail is ~37% of the 3.2s clip — but it's tail-only, so OK.
        _head_motion_tail_freeze_clip(clip, head_s=2.0, tail_s=1.2)
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.OK, (
            f"tail-only freeze should pass; got {reason.value}"
        )


def test_fully_frozen_clip_still_rejected_not_tail_excused():
    """A clip frozen end-to-end (no motion head) is still MOSTLY_FROZEN.

    The tail-aware exception must NOT excuse a pervasively frozen clip: there
    is no motion head, so the freeze is not a trailing hold.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "all_frozen.mp4")
        # A single still color frame held for the whole 4s — frozen head AND
        # tail. Non-black so the black check doesn't trip first.
        _ffmpeg([
            "-f", "lavfi", "-i", "color=c=blue:size=480x848:duration=4:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-t", "4", clip,
        ])
        reason = _run(validate_baked_clip(clip, block_id="b", render_id="r"))
        assert reason == ClipValidationReason.MOSTLY_FROZEN


def test_trailing_detect_start_open_to_eof():
    # A freeze that opens and never closes (runs to EOF) → trailing start.
    sample = (
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 2.0\n"
    )
    start = _trailing_detect_start(
        sample, "freeze", duration_s=3.2, eof_slack_s=0.3
    )
    assert start is not None and abs(start - 2.0) < 1e-6


def test_trailing_detect_start_closes_at_eof_within_slack():
    # Freeze closes within slack of the clip end → still trailing.
    sample = (
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 2.0\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 3.15\n"
    )
    start = _trailing_detect_start(
        sample, "freeze", duration_s=3.2, eof_slack_s=0.3
    )
    assert start is not None and abs(start - 2.0) < 1e-6


def test_trailing_detect_start_mid_clip_freeze_not_trailing():
    # A freeze that closes well before EOF is NOT a trailing hold.
    sample = (
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 0.5\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 1.0\n"
    )
    start = _trailing_detect_start(
        sample, "freeze", duration_s=3.2, eof_slack_s=0.3
    )
    assert start is None
