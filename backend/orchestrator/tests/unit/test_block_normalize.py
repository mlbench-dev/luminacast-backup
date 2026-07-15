"""Unit tests for services.block_normalize.normalize_baked_block.

These tests synthesize MP4 inputs via ffmpeg's lavfi sources so they
need no GPU, no faster-whisper, no network. ffmpeg + ffprobe must be on
PATH; tests are skipped otherwise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

from services.block_normalize import normalize_baked_block, _probe_streams


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; block_normalize tests need both",
)


def _synth_clip(
    *,
    out_path: str,
    width: int,
    height: int,
    duration_s: float,
    fps: int,
    with_audio: bool,
) -> None:
    """Generate a synthetic clip with testsrc2 + (optionally) sine."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:duration={duration_s}:rate={fps}",
    ]
    if with_audio:
        cmd.extend([
            "-f", "lavfi", "-i",
            f"sine=frequency=440:duration={duration_s}",
        ])
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"])
    cmd.extend([
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-t", f"{duration_s}",
        out_path,
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"synth ffmpeg failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )


def test_portrait_input_conformed_to_landscape_preserves_duration():
    """A 720x1280@25fps/5s portrait clip → 1114x828@30fps landscape with
    crop-cover scaling and an intact audio stream.

    Conform-only (Phase 1) is duration-PRESERVING: it does not trim to
    slot. The bake's own 5s length is kept; head-trimming to slot is the
    job of ``block_extension.trim_block_to_slot`` downstream.
    """
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        _synth_clip(
            out_path=in_path,
            width=720, height=1280,
            duration_s=5.0, fps=25,
            with_audio=True,
        )
        with open(in_path, "rb") as f:
            in_bytes = f.read()

        out_bytes = normalize_baked_block(
            input_bytes=in_bytes,
            target_width=1114,
            target_height=828,
            target_fps=30,
            target_duration_s=4.5,
            block_id="blk_test_portrait",
            render_id="rnd_test_portrait",
        )
        assert out_bytes, "normalize returned no bytes"

        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        probe = _probe_streams(out_path)
        assert probe["width"] == 1114, f"width was {probe['width']}"
        assert probe["height"] == 828, f"height was {probe['height']}"
        # fps probing is fps-rounding-tolerant: 30/1 reads as 30.0.
        assert 29.5 <= probe["fps"] <= 30.5, f"fps was {probe['fps']}"
        # Duration-preserving: output ≈ in_dur (5s), NOT the 4.5s slot.
        assert 4.95 <= probe["duration_s"] <= 5.10, (
            f"duration was {probe['duration_s']}s (expected ≈ in_dur=5s)"
        )
        assert probe["has_audio"], "expected audio stream after normalize"


def test_no_audio_input_gets_silent_track():
    """A video-only 3s clip → conform-only 3s slot=5s output (no extension).

    Conform-only normalize preserves the bake's own duration; duration
    shaping to the slot is the caller's responsibility, via
    ``services.block_extension.trim_block_to_slot``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        _synth_clip(
            out_path=in_path,
            width=1024, height=576,
            duration_s=3.0, fps=24,
            with_audio=False,
        )
        with open(in_path, "rb") as f:
            in_bytes = f.read()

        out_bytes = normalize_baked_block(
            input_bytes=in_bytes,
            target_width=1114,
            target_height=828,
            target_fps=30,
            target_duration_s=5.0,
            block_id="blk_test_silent",
            render_id="rnd_test_silent",
        )
        assert out_bytes

        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        probe = _probe_streams(out_path)
        assert probe["has_audio"], "video-only input should have a silent track added"
        # Conform-only: output duration ≈ in_dur (3s), NOT slot (5s).
        assert 2.95 <= probe["duration_s"] <= 3.10, (
            f"duration was {probe['duration_s']}s (expected ≈ in_dur=3s)"
        )
        assert probe["width"] == 1114
        assert probe["height"] == 828


def test_extend_to_slot_flag_is_now_a_noop():
    """``extend_to_slot=True`` is retained only for call-site compatibility
    and has NO effect: normalize is conform-only / duration-preserving.

    The pre-PR-#92 ping-pong extension ladder was deleted (it produced the
    "walks backwards" / freeze artefacts). Passing the legacy flag must NOT
    pad the clip up to slot — the 3s bake stays ≈ 3s, and duration shaping
    is owned downstream by ``block_extension.trim_block_to_slot``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        _synth_clip(
            out_path=in_path,
            width=1024, height=576,
            duration_s=3.0, fps=24,
            with_audio=False,
        )
        with open(in_path, "rb") as f:
            in_bytes = f.read()

        out_bytes = normalize_baked_block(
            input_bytes=in_bytes,
            target_width=1114,
            target_height=828,
            target_fps=30,
            target_duration_s=5.0,
            block_id="blk_test_legacy",
            render_id="rnd_test_legacy",
            extend_to_slot=True,
        )
        assert out_bytes

        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        probe = _probe_streams(out_path)
        # Flag is a no-op: duration is preserved at the bake's own ≈3s, NOT
        # padded up to the 5s slot.
        assert 2.95 <= probe["duration_s"] <= 3.10, (
            f"extend_to_slot must be a no-op (duration-preserving); "
            f"expected ≈3s, got {probe['duration_s']}s"
        )
