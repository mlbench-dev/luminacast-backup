"""Client report: "the talking head is showing behind [the product shot] —
when it's an image it's behind, when it's a video it's above." Confirmed:
not a z-order bug, a routing bug.

Root cause: cast_render.py resolves each PIP block's full-frame background
(so the talking head can be composited ON TOP of it in one pass) by scanning
overlay elements for one that's full-canvas-sized — but the scan only
accepted ``type == "video"``. A full-frame PHOTO background (e.g. an
AI-generated product still instead of a product video) was silently
skipped, so it never became the PIP's `bg_src` — instead it fell through to
the GENERIC overlay pass in worker_ffmpeg_compose.py, which runs AFTER the
PIP corner is already baked into the base timeline and paints the photo
straight over everything, hiding the face that's already there.

Even after accepting image types, a second bug had to be fixed: a still
image fed to ffmpeg via a plain ``-i`` decodes to exactly ONE frame. The PIP
filter graph's ``overlay=...:shortest=1`` steps cut the WHOLE composite down
to the shortest input — so without ``-loop 1`` (and matching ``-t``), a
PIP block with an image background collapsed to ~1 frame (~33ms) instead of
running the block's real slot duration; confirmed with real ffmpeg below
(0.033s without the fix vs the full 4.0s slot with it).

Tests: (a) source checks that cast_render.py now accepts image backgrounds
and tags them, and that worker_ffmpeg_compose.py loops them; (b) a real
ffmpeg reproduction of both the bug and the fix, including a pixel check
that the avatar box is actually visible over the background once fixed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_CAST_RENDER = _ORCH_ROOT / "tasks" / "cast_render.py"
_WORKER_COMPOSE = _ORCH_ROOT / "worker_ffmpeg_compose.py"


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_duration_s(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return float(out or 0)


# ── Source checks ────────────────────────────────────────────────────────

def test_cast_render_accepts_image_type_as_pip_background():
    src = _CAST_RENDER.read_text(encoding="utf-8")
    start = src.find("# ── PIP / talking-head geometry for the compose worker")
    assert start != -1
    end = src.find("\n    except Exception as _pip_exc:", start)
    assert end != -1
    body = src[start:end]
    assert '_ov.get("type") not in ("video", "image")' in body, (
        "the PIP-background scan must accept image overlays too, not just "
        "video — a photo b-roll behind a PIP block was silently skipped "
        "and fell through to the later, PIP-unaware overlay pass"
    )
    assert '_vt["pip"]["bg_kind"]' in body, (
        "must tag the resolved PIP background with its kind (image/video) "
        "so the compose worker knows whether to loop it"
    )


def test_worker_compose_loops_an_image_pip_background():
    src = _WORKER_COMPOSE.read_text(encoding="utf-8")
    start = src.find("pip = vt.get(\"pip\")")
    assert start != -1
    end = src.find("\n                pip_cmd = [", start)
    assert end != -1
    body = src[start:end]
    assert 'bg_kind = pip.get("bg_kind")' in body
    assert '"-loop", "1"' in body, (
        "a still-image PIP background must be looped for the slot duration "
        "— without it ffmpeg decodes exactly one frame and the "
        "shortest=1 overlay chain collapses the whole composite to that "
        "single frame"
    )


# ── Real ffmpeg: reproduce the bug, then the fix ────────────────────────

pytestmark = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def _build_pip_inputs_and_filter(*, canvas_w, canvas_h, canvas_fps, slot_dur, pw, ph, px, py, norm, bg_local, loop_image: bool):
    pip_inputs = [
        "-f", "lavfi", "-t", f"{slot_dur:.3f}",
        "-i", f"color=c=black:s={canvas_w}x{canvas_h}:r={canvas_fps}",
        "-i", norm,
    ]
    if loop_image:
        pip_inputs += ["-loop", "1", "-t", f"{slot_dur:.3f}", "-i", bg_local]
    else:
        pip_inputs += ["-i", bg_local]
    fc = (
        f"[2:v]fps={canvas_fps},scale={canvas_w}:{canvas_h}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={canvas_w}:{canvas_h},setsar=1[bg];"
        f"[0:v][bg]overlay=0:0:shortest=1[base];"
        f"[1:v]scale={pw}:{ph}:force_original_aspect_ratio=increase,"
        f"crop={pw}:{ph},setsar=1[fg];"
        f"[base][fg]overlay={px}:{py}:shortest=1[vout]"
    )
    return pip_inputs, fc


def test_image_background_without_loop_collapses_to_one_frame():
    """Reproduces the bug exactly: an image PIP background fed via plain
    -i (the old behaviour) collapses the whole composite to ~1 frame."""
    with tempfile.TemporaryDirectory() as tmp:
        canvas_w, canvas_h, canvas_fps, slot_dur = 1080, 1920, 30, 4.0
        norm = os.path.join(tmp, "norm.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c=red:s={canvas_w}x{canvas_h}:d={slot_dur}:r={canvas_fps}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", norm],
            capture_output=True, check=True,
        )
        bg_local = os.path.join(tmp, "pipbg.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={canvas_w}x{canvas_h}",
             "-frames:v", "1", bg_local],
            capture_output=True, check=True,
        )
        pip_inputs, fc = _build_pip_inputs_and_filter(
            canvas_w=canvas_w, canvas_h=canvas_h, canvas_fps=canvas_fps, slot_dur=slot_dur,
            pw=500, ph=500, px=40, py=1340, norm=norm, bg_local=bg_local, loop_image=False,
        )
        out = os.path.join(tmp, "old.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *pip_inputs, "-filter_complex", fc,
               "-map", "[vout]", "-r", str(canvas_fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", out]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        dur = _probe_duration_s(out)
        assert dur < 0.2, f"expected the bug's ~1-frame collapse, got {dur}s"


def test_image_background_with_loop_runs_full_slot_and_shows_the_face():
    """The fix: -loop 1 (+ matching -t) keeps the composite at the real
    slot duration, with BOTH the background image and the avatar's PIP
    box visible at mid-slot — avatar correctly on top, not hidden."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        canvas_w, canvas_h, canvas_fps, slot_dur = 1080, 1920, 30, 4.0
        pw, ph, px, py = 500, 500, 40, 1340

        norm = os.path.join(tmp, "norm.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c=red:s={canvas_w}x{canvas_h}:d={slot_dur}:r={canvas_fps}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", norm],
            capture_output=True, check=True,
        )
        bg_local = os.path.join(tmp, "pipbg.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={canvas_w}x{canvas_h}",
             "-frames:v", "1", bg_local],
            capture_output=True, check=True,
        )
        pip_inputs, fc = _build_pip_inputs_and_filter(
            canvas_w=canvas_w, canvas_h=canvas_h, canvas_fps=canvas_fps, slot_dur=slot_dur,
            pw=pw, ph=ph, px=px, py=py, norm=norm, bg_local=bg_local, loop_image=True,
        )
        out = os.path.join(tmp, "fixed.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *pip_inputs, "-filter_complex", fc,
               "-map", "[vout]", "-r", str(canvas_fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", out]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)

        dur = _probe_duration_s(out)
        assert abs(dur - slot_dur) < 0.2, f"expected ~{slot_dur}s, got {dur}s"

        frame_path = os.path.join(tmp, "frame.png")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "2.0", "-i", out, "-frames:v", "1", frame_path],
            capture_output=True, check=True, timeout=30,
        )
        img = Image.open(frame_path).convert("RGB")
        bg_px = img.getpixel((10, 10))
        fg_px = img.getpixel((px + pw // 2, py + ph // 2))
        assert bg_px[2] > bg_px[0] and bg_px[2] > bg_px[1], f"background not blue: {bg_px}"
        assert fg_px[0] > fg_px[1] and fg_px[0] > fg_px[2], f"avatar box not red (hidden?): {fg_px}"
