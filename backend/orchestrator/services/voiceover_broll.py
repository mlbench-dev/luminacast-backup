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
    with output length. Budget ~6x real-time plus a 60s floor for process
    spin-up so a short slot still gets a usable timeout. No hardcoded render
    timeouts — this is the only place the value is computed.

    The floor was 30s until every block in a cast was found to dispatch
    concurrently (tasks/cast_render.py runs all blocks via asyncio.gather) —
    a real render commonly bakes 3-5 of these clips at once alongside the
    AI-cascade blocks, and on an 8-core dev machine that's enough libx264
    contention to blow a 30s budget on a plain ~5s clip even though the same
    encode takes 1-2s in isolation. Reproduced directly: running 5 of a real
    cast's voiceover/stock blocks concurrently timed out 3 of them at exactly
    30.0s while 2 finished fine. Paired with _BROLL_CONCURRENCY_LIMIT below,
    which caps how many of these encodes fight for CPU at once in the first
    place — the bigger floor is headroom, not the primary fix.
    """
    return max(60.0, float(slot_s or 0.0) * 6.0)


# Caps how many B-roll ffmpeg encodes (Ken-Burns / video-to-slot) run at once
# across the whole process. Every block in a cast dispatches concurrently
# (tasks/cast_render.py's asyncio.gather), and libx264 encodes are CPU-bound —
# letting all of them race for cores at once is what produced the timeouts
# above. This does not affect the AI-cascade (WaveSpeed/fal) blocks, which
# are network-bound, not CPU-bound, and already serialize the on-prem GPU
# separately via render_dispatcher's own semaphore.
_BROLL_CONCURRENCY_LIMIT = max(2, (os.cpu_count() or 4) // 2)

# Per-event-loop semaphore, not a module-level singleton — a plain
# module-singleton asyncio.Semaphore binds to whatever event loop is
# running at construction time, and a Celery worker that respawns its loop
# between tasks ends up reusing a semaphore bound to a dead loop, raising
# "Semaphore ... is bound to a different event loop" the moment a new task
# tries to acquire it (confirmed in production: 7d of render logs showed
# this as the single largest failure category for voiceover/B-roll blocks).
# Same fix already applied to the HOSTKEY GPU semaphore in
# render_dispatcher.py's `_get_loop_semaphore` — mirrored here.
_broll_loop_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_broll_semaphore() -> asyncio.Semaphore:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
        # No running loop somehow — fall back to an unshared one-shot
        # semaphore rather than crash; worst case this one call runs
        # unserialized against the CPU-bound encode limit.
        return asyncio.Semaphore(_BROLL_CONCURRENCY_LIMIT)
    key = id(loop)
    sem = _broll_loop_semaphores.get(key)
    if sem is None:
        sem = asyncio.Semaphore(_BROLL_CONCURRENCY_LIMIT)
        _broll_loop_semaphores[key] = sem
    return sem


async def _download(url: str, dest_path: str, *, timeout_s: float) -> None:
    """Download ``url`` to ``dest_path``, retrying once on a transient failure.

    A full cast render fires many concurrent downloads at once (TTS audio,
    every block's B-roll source, avatar-cascade payloads), which occasionally
    trips a single connection reset / timeout even though the same URL
    downloads fine in isolation — the caller has no fallback for this block
    once it raises (it skips the bake entirely, landing the slot on the
    canvas background), so one retry is cheap insurance against exactly that
    class of blip. Matches the retry-once pattern already used for TTS
    generation (tasks/generate_cast.py).
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                with open(dest_path, "wb") as fh:
                    fh.write(resp.content)
            return
        except Exception as e:
            last_exc = e
            if attempt == 1:
                await asyncio.sleep(1.5)
    assert last_exc is not None
    raise last_exc


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

    # Gate the actual CPU-bound encode behind a concurrency limit — see
    # _BROLL_CONCURRENCY_LIMIT. The download that precedes this call is not
    # gated (it's I/O-bound and isn't what starved these processes of CPU).
    async with _get_broll_semaphore():
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
