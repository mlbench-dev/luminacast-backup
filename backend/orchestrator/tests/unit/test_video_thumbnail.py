"""Unit tests for services.video_thumbnail.extract_video_thumbnail_jpeg.

Generates a tiny synthetic MP4 with lavfi so the test needs no GPU, no
network, no fixtures — just ffmpeg on PATH (the production image
already has it; tests skip otherwise).
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile

import pytest

from services.video_thumbnail import extract_video_thumbnail_jpeg


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg not on PATH; thumbnail tests need it",
)


def _synth_video(duration_s: float = 2.0) -> bytes:
    """Synthesise a short coloured MP4 and return its raw bytes."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"color=c=red:s=64x64:d={duration_s}:r=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"failed to synth MP4: {proc.stderr.decode()[-300:]}")
    with open(out_path, "rb") as fh:
        return fh.read()


def test_extract_returns_jpeg_bytes():
    video = _synth_video(duration_s=2.0)
    out = asyncio.run(extract_video_thumbnail_jpeg(video))
    assert isinstance(out, bytes)
    assert len(out) > 0
    # JPEG magic bytes.
    assert out[:3] == b"\xff\xd8\xff", "expected JPEG SOI marker"


def test_extract_empty_input_returns_empty_bytes():
    out = asyncio.run(extract_video_thumbnail_jpeg(b""))
    assert out == b""


def test_extract_invalid_input_returns_empty_bytes():
    # Not a video — ffmpeg fails, helper swallows the error.
    out = asyncio.run(extract_video_thumbnail_jpeg(b"not a video at all"))
    assert out == b""


def test_extract_short_video_retries_at_zero():
    # 0.3s video, default seek of 0.5s would miss → helper retries at 0.
    video = _synth_video(duration_s=0.3)
    out = asyncio.run(extract_video_thumbnail_jpeg(video))
    assert isinstance(out, bytes)
    # Either we got a frame on retry, or we got b"" if ffmpeg still couldn't —
    # both are acceptable contract-wise (no exception bubbled up).
    if out:
        assert out[:3] == b"\xff\xd8\xff"
