"""Unit tests for the Phase 2 speaking-block tolerance gate.

``tasks.cast_render._enforce_speaking_tolerance`` enforces a [-2%, +5%]
duration band around a speaking block's slot:

  * within band            → clip returned untouched.
  * long but within +5%    → END-trimmed to slot (no pad / stretch).
  * outside the band        → raises ``SpeakingBlockOutOfTolerance`` so the
    existing per-block retry-once pass re-bakes it (never padded).

Inputs are synthesized via ffmpeg lavfi (no GPU / network); skipped when
ffmpeg/ffprobe are absent.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

from tasks.cast_render import (
    _enforce_speaking_tolerance,
    SpeakingBlockOutOfTolerance,
)
from services.block_normalize import _probe_streams


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; speaking-tolerance tests need both",
)


def _synth_clip(out_path: str, *, duration_s: float, fps: int = 30) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=size=480x848:duration={duration_s}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration_s}",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")


def _timeline_with_slot(block_id: str, slot_s: float) -> dict:
    """Minimal timeline whose V1 element gives _slot_duration_for_block a slot."""
    return {
        "compositionWidth": 480,
        "compositionHeight": 848,
        "fps": 30,
        "tracks": [
            {
                "type": "video",
                "elements": [
                    {
                        "id": f"v1_{block_id}",
                        "s": 0.0,
                        "e": slot_s,
                        "metadata": {"block_id": block_id},
                        "props": {},
                    },
                ],
            },
        ],
    }


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _bytes_for(clip_path: str) -> bytes:
    with open(clip_path, "rb") as f:
        return f.read()


def test_within_band_passthrough():
    """A clip ≈ slot (well inside [-2%, +5%]) is returned untouched."""
    block_id = "blk_spk_ok"
    slot_s = 5.0
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "in.mp4")
        _synth_clip(clip, duration_s=5.0)  # exactly slot
        in_bytes = _bytes_for(clip)

        out_bytes = _run(
            _enforce_speaking_tolerance(
                in_bytes,
                timeline=_timeline_with_slot(block_id, slot_s),
                block_id=block_id,
                render_id="rnd_spk_ok",
                fallback_duration_s=slot_s,
            )
        )
        # Untouched: identical bytes back.
        assert out_bytes == in_bytes


def test_long_within_tolerance_end_trimmed_to_slot():
    """A clip long by +4% (inside +5%) is END-trimmed down to slot."""
    block_id = "blk_spk_long"
    slot_s = 5.0
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "in.mp4")
        _synth_clip(clip, duration_s=5.20)  # +4% over slot
        in_bytes = _bytes_for(clip)

        out_bytes = _run(
            _enforce_speaking_tolerance(
                in_bytes,
                timeline=_timeline_with_slot(block_id, slot_s),
                block_id=block_id,
                render_id="rnd_spk_long",
                fallback_duration_s=slot_s,
            )
        )
        assert out_bytes != in_bytes, "long clip should have been end-trimmed"
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 4.90 <= probe["duration_s"] <= 5.10, (
            f"end-trim should land on slot=5.0s; got {probe['duration_s']:.3f}s"
        )


def test_too_short_raises_for_retry():
    """A clip short by ~10% (below -2%) raises SpeakingBlockOutOfTolerance."""
    block_id = "blk_spk_short"
    slot_s = 5.0
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "in.mp4")
        _synth_clip(clip, duration_s=4.5)  # -10% — dropped speech
        in_bytes = _bytes_for(clip)

        with pytest.raises(SpeakingBlockOutOfTolerance):
            _run(
                _enforce_speaking_tolerance(
                    in_bytes,
                    timeline=_timeline_with_slot(block_id, slot_s),
                    block_id=block_id,
                    render_id="rnd_spk_short",
                    fallback_duration_s=slot_s,
                )
            )


def test_too_long_beyond_tolerance_raises_for_retry():
    """A clip long by ~20% (above +5%) raises rather than end-trimming."""
    block_id = "blk_spk_overrun"
    slot_s = 5.0
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "in.mp4")
        _synth_clip(clip, duration_s=6.0)  # +20% over-run
        in_bytes = _bytes_for(clip)

        with pytest.raises(SpeakingBlockOutOfTolerance):
            _run(
                _enforce_speaking_tolerance(
                    in_bytes,
                    timeline=_timeline_with_slot(block_id, slot_s),
                    block_id=block_id,
                    render_id="rnd_spk_overrun",
                    fallback_duration_s=slot_s,
                )
            )
