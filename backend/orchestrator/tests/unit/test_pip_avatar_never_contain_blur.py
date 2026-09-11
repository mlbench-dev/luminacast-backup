"""Client report: "the talking head is showing behind [something] — it was
not like this before / in the preview it's showing correctly, the issue is
in the rendered video." A PIP-layout avatar (small corner talking-head
window over a product/b-roll background) rendered as a washed-out sliver
instead of a clear face.

Root cause: the contain-fit + blurred-backdrop fix (block_normalize.py,
services.aspect_conform) was correct for FULLSCREEN mismatched-aspect
bakes, but was applied unconditionally to EVERY bake, including a PIP
block's — and a PIP bake is never the final on-screen content itself, only
a SOURCE the compositor (worker_ffmpeg_compose.py's PIP overlay step)
cover-crops a SECOND time down into a small corner window. Padding the
face into the middle of a blurred, canvas-sized frame and then
cover-cropping THAT down to a tiny box crops into the blurred padding
instead of the face — the talking head disappears into its own backdrop,
reading as "showing behind" whatever's in front of / around it.

Fix: normalize_baked_block gained `force_cover` — when the block's bonded
V1 element carries `metadata.render_mode == "pip"` (detected by the new
_block_is_pip_source helper in cast_render.py), the conform step always
uses plain cover-crop regardless of aspect divergence, exactly matching
the pre-aspect-conform-fix behaviour for PIP blocks specifically. Fullscreen
blocks are completely unaffected (force_cover defaults to False, unchanged
from before this fix).

Pure/real-ffmpeg tests — no DB, no network, no celery.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from services.block_normalize import normalize_baked_block, _probe_streams
from services.aspect_conform import build_conform_filter

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_CAST_RENDER = _ORCH_ROOT / "tasks" / "cast_render.py"


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH",
)


def _synth_clip(out_path: str, width: int, height: int, duration_s: float, fps: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:duration={duration_s}:rate={fps}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-t", f"{duration_s}", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-500:]}")


def test_force_cover_skips_contain_blur_even_for_a_heavily_diverging_source():
    """The exact PIP regression scenario: a portrait talking-head bake
    conformed toward a landscape canvas. Without force_cover this would
    hit the contain+blur branch; with it, it must stay plain cover-crop."""
    forced = build_conform_filter(
        in_label="0:v", out_label="vprep", target_w=1920, target_h=1080,
        src_w=720, src_h=1280, threshold=0.0,
    )
    assert "split=2" not in forced
    assert "gblur" not in forced
    assert "force_original_aspect_ratio=increase" in forced

    unforced = build_conform_filter(
        in_label="0:v", out_label="vprep", target_w=1920, target_h=1080,
        src_w=720, src_h=1280,
    )
    assert "split=2" in unforced and "gblur" in unforced


def test_normalize_baked_block_force_cover_real_ffmpeg():
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(src_path, 720, 1280, 2.0, 25)
        with open(src_path, "rb") as f:
            video_bytes = f.read()

        out_bytes = normalize_baked_block(
            input_bytes=video_bytes,
            target_width=1920, target_height=1080, target_fps=30,
            target_duration_s=2.0,
            block_id="blk_pip", render_id="rnd_pip",
            force_cover=True,
        )
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert probe["width"] == 1920
        assert probe["height"] == 1080


def test_default_behaviour_unchanged_for_fullscreen_blocks():
    """force_cover defaults to False — a fullscreen block's diverging bake
    must still get the contain+blur treatment, unaffected by this fix."""
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(src_path, 720, 1280, 2.0, 25)
        with open(src_path, "rb") as f:
            video_bytes = f.read()

        out_bytes = normalize_baked_block(
            input_bytes=video_bytes,
            target_width=1920, target_height=1080, target_fps=30,
            target_duration_s=2.0,
            block_id="blk_full", render_id="rnd_full",
        )
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert probe["width"] == 1920
        assert probe["height"] == 1080


def _load_block_is_pip_source():
    src = _CAST_RENDER.read_text(encoding="utf-8")
    start = src.find("def _block_is_pip_source(")
    assert start != -1
    end = src.find("\n\n\n", start)
    assert end != -1
    ns: dict = {}
    exec(src[start:end], ns)  # noqa: S102 — trusted, local source slice
    return ns["_block_is_pip_source"]


def test_block_is_pip_source_detects_pip_render_mode():
    fn = _load_block_is_pip_source()
    timeline = {
        "tracks": [{"elements": [
            {"type": "video", "metadata": {"block_id": "b1", "bonded": True, "render_mode": "pip"}},
        ]}],
    }
    assert fn(timeline, "b1") is True


def test_block_is_pip_source_false_for_fullscreen_and_unknown_blocks():
    fn = _load_block_is_pip_source()
    timeline = {
        "tracks": [{"elements": [
            {"type": "video", "metadata": {"block_id": "b1", "bonded": True, "render_mode": "full"}},
        ]}],
    }
    assert fn(timeline, "b1") is False
    assert fn(timeline, "b_never_seen") is False


def test_block_is_pip_source_handles_missing_or_malformed_timeline():
    fn = _load_block_is_pip_source()
    assert fn(None, "b1") is False
    assert fn({}, "b1") is False
    assert fn({"tracks": [{"elements": [None, {"metadata": None}]}]}, "b1") is False


def test_normalize_for_canvas_passes_force_cover_from_pip_detection():
    src = (_ORCH_ROOT / "tasks" / "cast_render.py").read_text(encoding="utf-8")
    start = src.find("async def _normalize_for_canvas(")
    assert start != -1
    end = src.find("\nasync def ", start + 1)
    body = src[start:end if end != -1 else None]
    assert "force_cover=_block_is_pip_source(timeline, block_id)" in body
