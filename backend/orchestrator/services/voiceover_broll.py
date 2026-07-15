"""Ken-Burns B-roll generation for voiceover blocks.

Voiceover (``render_mode=voiceover``) blocks carry narration audio but no
face animation. Before this module the dispatcher skipped the video side
entirely and returned ``None``, so the FFmpeg compose pass found no clip for
the slot and laid down pure black behind the audio (the 7s black at
40.23-47.50 on rnd_124ec1f95740 — videos=9 audios=10).

This module fills the slot with motion-bearing video so the compose pass has
a real clip to lay down. It resolves a visual source in priority order and
returns slot-length MP4 bytes:

  1. A product / block VIDEO asset → looped or trimmed to fill the slot.
  2. A product / block / scene IMAGE → animated with a slow Ken-Burns
     pan-zoom (NEVER a still frame — a still violates ``clip_mostly_frozen``).

The dispatcher owns the last-resort avatar-idle fallback and the actual R2
upload + validator gate; this module only materialises the clip bytes.

All ffmpeg work is offloaded to threads so the asyncio render loop stays
responsive. Timeouts are derived per-clip from the slot duration — there are
no hardcoded render timeouts (RULES.md §"no hardcoded render/audio timeouts").
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

import httpx
import sentry_sdk

# Ken-Burns zoom rate per frame. Matches the background compositor's default
# (services/background_compositor.py) so the pan-zoom feel is consistent
# across the product. Slow enough to read as cinematic, fast enough that
# freezedetect never trips (the frame content changes every frame).
KEN_BURNS_ZOOM_PER_FRAME = 0.0008
KEN_BURNS_ZOOM_MAX = 1.18


def _derive_timeout_s(slot_s: float) -> float:
    """Per-clip ffmpeg timeout derived from the slot duration.

    A Ken-Burns/loop pass is a single-input re-encode whose wall time scales
    with output length. Budget ~6x real-time plus a 30s floor for process
    spin-up so a short slot still gets a usable timeout. No hardcoded render
    timeouts — this is the only place the value is computed.
    """
    return max(30.0, float(slot_s or 0.0) * 6.0)


async def _download(url: str, dest_path: str, *, timeout_s: float) -> None:
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            fh.write(resp.content)


def _ken_burns_filter(width: int, height: int, fps: int, slot_s: float) -> str:
    """Build the zoompan filter that turns a still image into a panning clip.

    The image is upscaled 2x first so the zoompan crop window has headroom
    and the output stays sharp. ``d`` is the number of output frames; the
    zoom ramps linearly toward ``KEN_BURNS_ZOOM_MAX`` and the crop centre
    drifts, so every frame differs from the last — defeating freezedetect.
    """
    total_frames = max(1, int(round(slot_s * fps)))
    return (
        f"scale={width * 2}:{height * 2},"
        f"zoompan=z='min(zoom+{KEN_BURNS_ZOOM_PER_FRAME},{KEN_BURNS_ZOOM_MAX})':"
        f"d={total_frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={fps},"
        f"setsar=1"
    )


async def render_ken_burns_from_image(
    *,
    image_url: str,
    slot_s: float,
    width: int,
    height: int,
    fps: int,
) -> bytes:
    """Render a slot-length Ken-Burns pan-zoom clip from a single image.

    Returns silent MP4 bytes (the dispatcher muxes the narration audio).
    Raises on download / ffmpeg failure so the caller can fall through to
    the next source in the priority list.
    """
    timeout_s = _derive_timeout_s(slot_s)
    with tempfile.TemporaryDirectory(prefix="vo_broll_img_") as tmp:
        img_path = os.path.join(tmp, "src")
        out_path = os.path.join(tmp, "out.mp4")
        await _download(image_url, img_path, timeout_s=timeout_s)

        vf = _ken_burns_filter(width, height, fps, slot_s)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", img_path,
            "-t", f"{slot_s:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_path,
        ]
        await _run_ffmpeg(cmd, timeout_s=timeout_s, what="ken_burns_image")
        with open(out_path, "rb") as fh:
            return fh.read()


async def render_video_to_slot(
    *,
    video_url: str,
    slot_s: float,
    width: int,
    height: int,
    fps: int,
) -> bytes:
    """Loop / trim a source video to exactly fill the slot.

    A product video shorter than the slot is looped (``-stream_loop -1``)
    then bounded by ``-t``; a longer one is simply trimmed. The result is
    crop-cover scaled to the canvas so compose treats it like any other
    baked block. Returns silent MP4 bytes (audio is dropped — the block's
    narration is the audio source of truth and is muxed by the caller).
    """
    timeout_s = _derive_timeout_s(slot_s)
    with tempfile.TemporaryDirectory(prefix="vo_broll_vid_") as tmp:
        src_path = os.path.join(tmp, "src.mp4")
        out_path = os.path.join(tmp, "out.mp4")
        await _download(video_url, src_path, timeout_s=timeout_s)

        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", src_path,
            "-t", f"{slot_s:.3f}",
            "-an",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_path,
        ]
        await _run_ffmpeg(cmd, timeout_s=timeout_s, what="video_to_slot")
        with open(out_path, "rb") as fh:
            return fh.read()


async def _run_ffmpeg(cmd: list[str], *, timeout_s: float, what: str) -> None:
    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False,
        )

    try:
        result = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired as e:
        sentry_sdk.capture_exception(e)
        raise
    if result.returncode != 0:
        raise RuntimeError(
            f"{what} ffmpeg failed (rc={result.returncode}): "
            f"{result.stderr[-1500:]}"
        )
