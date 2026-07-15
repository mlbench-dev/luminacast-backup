"""Regression tests for the motion-bake download truncation bug.

Evidence (rnd_247b0379bcd9, blk_45e53220b38c): fal delivered a genuine 12s
Wan I2V clip, but the file reaching ``normalize`` was ~5.1s — exactly the
length of the (short) voiceover TTS. Root cause: ``_mux_audio_into_clip``
muxed the TTS over the video with ``-shortest``, clipping the longer video
down to the shorter audio. The fix pads short audio with silence and bounds
the output to the video's own duration, so the visual is never cut.

These tests synthesize clips with ffmpeg lavfi (no GPU / network) and patch
the in-function audio download so the mux helper runs fully offline. Skipped
when ffmpeg/ffprobe are absent. ``sentry_sdk`` is a real dependency — never
stubbed.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from unittest import mock

import pytest

import tasks.cast_render as cast_render
from tasks.cast_render import (
    MotionDownloadTruncationError,
    _mux_audio_into_clip,
    _probe_bytes_duration_s,
)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; motion mux duration tests need both",
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", *args],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")


def _synth_video_bytes(duration_s: float) -> bytes:
    """Silent moving-pattern mp4 of the requested length, as raw bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v.mp4")
        _ffmpeg([
            "-f", "lavfi", "-i",
            f"testsrc2=size=320x568:duration={duration_s}:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", "-t", f"{duration_s}", path,
        ])
        with open(path, "rb") as fh:
            return fh.read()


def _synth_audio_file(path: str, duration_s: float) -> None:
    _ffmpeg([
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration_s}", path,
    ])


@asynccontextmanager
async def _fake_async_client(audio_path: str):
    """Stand-in for ``httpx.AsyncClient`` that serves a local audio file."""
    with open(audio_path, "rb") as fh:
        audio_bytes = fh.read()

    class _Resp:
        content = audio_bytes

        def raise_for_status(self):
            return None

    class _Client:
        async def get(self, *a, **k):
            return _Resp()

    yield _Client()


def _patch_audio_download(audio_path: str):
    """Patch the module-level httpx so the mux helper reads a local audio."""
    fake = mock.Mock()
    fake.AsyncClient = lambda *a, **k: _fake_async_client(audio_path)
    return mock.patch.object(cast_render, "httpx", fake)


def test_probe_bytes_duration_roundtrips():
    video_bytes = _synth_video_bytes(7.0)
    dur = _run(_probe_bytes_duration_s(video_bytes))
    assert abs(dur - 7.0) < 0.3


def test_probe_bytes_duration_empty_is_zero():
    assert _run(_probe_bytes_duration_s(b"")) == 0.0


def test_mux_preserves_video_when_audio_shorter():
    # The bug: 12s video + ~5s TTS → -shortest clipped video to ~5s.
    # The fix pads the audio with silence; the video stays full length.
    video_bytes = _synth_video_bytes(12.0)
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = os.path.join(tmp, "a.m4a")
        _synth_audio_file(audio_path, 5.0)
        with _patch_audio_download(audio_path):
            muxed = _run(
                _mux_audio_into_clip(
                    video_bytes, "https://example.test/voice.m4a",
                    duration_s=12.0,
                )
            )
    out_dur = _run(_probe_bytes_duration_s(muxed))
    # Video must NOT be cut to the 5s audio; it stays at the source 12s.
    assert out_dur > 11.0, f"video truncated to {out_dur:.2f}s (expected ~12s)"


def test_mux_bounds_video_when_audio_longer():
    # An over-long TTS must not extend the visual past its own duration.
    video_bytes = _synth_video_bytes(5.0)
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = os.path.join(tmp, "a.m4a")
        _synth_audio_file(audio_path, 12.0)
        with _patch_audio_download(audio_path):
            muxed = _run(
                _mux_audio_into_clip(
                    video_bytes, "https://example.test/voice.m4a",
                    duration_s=5.0,
                )
            )
    out_dur = _run(_probe_bytes_duration_s(muxed))
    assert abs(out_dur - 5.0) < 0.4, f"video duration drifted to {out_dur:.2f}s"


def test_truncation_error_is_runtime_error_subtype():
    # The gate raises this so a half-length clip fails loud instead of
    # silently producing a downstream duration_undershoot.
    assert issubclass(MotionDownloadTruncationError, RuntimeError)
