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
import logging
import os
import subprocess
import tempfile

import httpx
import sentry_sdk

logger = logging.getLogger(__name__)

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
    fade_in_s: float = 0.0,
    fade_out_s: float = 0.0,
) -> bytes:
    """Render a slot-length Ken-Burns pan-zoom clip from a single image.

    Returns silent MP4 bytes (the dispatcher muxes the narration audio).
    Raises on download / ffmpeg failure so the caller can fall through to
    the next source in the priority list.

    No ``playback_rate`` param — there's no source playback speed for a
    still image; "rate" would mean speeding up/slowing down the pan/zoom
    motion itself, a different change to ``_ken_burns_filter``'s own
    per-frame zoom math, out of scope here (this block's b-roll is always a
    real video in the confirmed case this fixes).
    """
    timeout_s = _derive_timeout_s(slot_s)
    with tempfile.TemporaryDirectory(prefix="vo_broll_img_") as tmp:
        img_path = os.path.join(tmp, "src")
        out_path = os.path.join(tmp, "out.mp4")
        await _download(image_url, img_path, timeout_s=timeout_s)

        # _ken_burns_filter's own scale=width*2:height*2 step assumes the
        # source is already roughly the canvas shape — it has no
        # force_original_aspect_ratio guard, so a portrait product/scene
        # photo fed straight in gets squashed/stretched to fit a landscape
        # canvas (or vice versa). Pre-conform the still to (width, height)
        # here: contain-fit + blurred backdrop when the shapes diverge (the
        # avatar/product-photo-is-always-portrait bug), a cheap cover-crop
        # resize when they're already close. Either way the Ken Burns
        # filter below then receives an image already the right shape, so
        # its 2x scale is undistorted.
        try:
            with open(img_path, "rb") as _imf:
                _img_bytes = _imf.read()
            from services.aspect_conform import conform_image_bytes
            _conformed = await asyncio.to_thread(
                conform_image_bytes, _img_bytes, width, height,
            )
            with open(img_path, "wb") as _imf:
                _imf.write(_conformed)
        except Exception as conform_exc:
            sentry_sdk.capture_exception(conform_exc)
            logger.warning(
                "Ken Burns source conform failed (%s); using raw image "
                "as downloaded — may distort on aspect mismatch",
                conform_exc,
            )

        vf = _ken_burns_filter(width, height, fps, slot_s)
        fade_filter = ""
        if fade_in_s > 0:
            fade_filter += f",fade=t=in:st=0:d={fade_in_s:.3f}"
        if fade_out_s > 0:
            fade_out_start = max(0.0, slot_s - fade_out_s)
            fade_filter += f",fade=t=out:st={fade_out_start:.3f}:d={fade_out_s:.3f}"
        vf = f"{vf}{fade_filter}"
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
    playback_rate: float = 1.0,
    fade_in_s: float = 0.0,
    fade_out_s: float = 0.0,
) -> bytes:
    """Loop / trim a source video to exactly fill the slot.

    A product video shorter than the slot is looped (``-stream_loop -1``)
    then bounded by a ``trim`` filter; a longer one is simply trimmed. The
    result is crop-cover scaled to the canvas so compose treats it like any
    other baked block. Returns silent MP4 bytes (audio is dropped — the
    block's narration is the audio source of truth and is muxed by the
    caller — so ``playback_rate`` only affects this VISUAL b-roll, never the
    narration; unlike an avatar talking-head clip there's no lipsync to keep
    in step with).

    ``playback_rate``/``fade_in_s``/``fade_out_s`` come from the editor's
    per-clip settings on this block's b-roll timeline item — previously
    read and saved correctly but never actually reaching this bake step, so
    they silently did nothing on render.
    """
    timeout_s = _derive_timeout_s(slot_s)
    rate = playback_rate if playback_rate and playback_rate > 0 else 1.0
    with tempfile.TemporaryDirectory(prefix="vo_broll_vid_") as tmp:
        src_path = os.path.join(tmp, "src.mp4")
        out_path = os.path.join(tmp, "out.mp4")
        await _download(video_url, src_path, timeout_s=timeout_s)

        # Product/b-roll videos are frequently portrait even when the cast
        # canvas is horizontal — a plain cover-crop then has to blow the clip
        # up and throw away most of one dimension to fill the frame (the
        # "horizontal video is heavily zoomed in" bug). Only fall back to
        # contain-fit + blurred backdrop when the shapes actually diverge;
        # same-shape sources keep the original cover-crop unchanged.
        src_w = src_h = 0
        try:
            from services.block_normalize import _probe_streams
            probe = await asyncio.to_thread(_probe_streams, src_path)
            src_w = int(probe.get("width") or 0)
            src_h = int(probe.get("height") or 0)
        except Exception as probe_exc:
            sentry_sdk.capture_exception(probe_exc)

        # build_conform_filter's contain-fit branch uses split + named pads
        # to merge a blurred backdrop with a contain-fit foreground, which
        # -vf's simple linear chain can't express — use -filter_complex
        # with an explicit -map instead (works for both branches).
        #
        # Rate change goes BEFORE the conform step (setpts scales how much
        # wall-clock time the source occupies) and the exact-duration trim
        # + fades go AFTER it, in the conformed stream's own coordinates —
        # mirrors the same rate-then-conform-then-trim-then-fade ordering
        # used for avatar/talking-head clips in worker_ffmpeg_compose.py.
        # No explicit input `-t` anymore: `-stream_loop -1` makes the input
        # effectively infinite and the `trim` filter below is what actually
        # bounds the output, which composes correctly with a rate change
        # (an input-side `-t` would instead cut off after slot_s of RAW
        # source time, before rate-scaling had a chance to apply).
        rate_prefix = f"setpts={(1.0 / rate):.6f}*PTS," if rate != 1.0 else ""
        fade_filter = ""
        if fade_in_s > 0:
            fade_filter += f",fade=t=in:st=0:d={fade_in_s:.3f}"
        if fade_out_s > 0:
            fade_out_start = max(0.0, slot_s - fade_out_s)
            fade_filter += f",fade=t=out:st={fade_out_start:.3f}:d={fade_out_s:.3f}"

        from services.aspect_conform import build_conform_filter
        filter_complex = build_conform_filter(
            in_label="0:v", out_label="conformed",
            target_w=width, target_h=height,
            src_w=src_w, src_h=src_h,
            extra_pre=f"{rate_prefix}fps={fps},",
        )
        filter_complex += (
            f";[conformed]trim=duration={slot_s:.3f},"
            f"setpts=PTS-STARTPTS{fade_filter}[vout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", src_path,
            "-an",
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_path,
        ]
        await _run_ffmpeg(cmd, timeout_s=timeout_s, what="video_to_slot")
        with open(out_path, "rb") as fh:
            return fh.read()


async def concat_video_clips(clips: list[bytes]) -> bytes:
    """Stream-copy concatenate already-baked clips into one continuous video,
    in order, with a plain hard cut at each seam.

    Every clip must share the same codec/resolution/fps/pix_fmt — true of any
    combination of ``render_video_to_slot`` / ``render_ken_burns_from_image``
    output, since both always encode to the same canvas at
    libx264/yuv420p/faststart. The concat DEMUXER used here (``-c copy``) is
    a pure stream copy, not a re-encode — that's also what keeps the cut
    clean: no re-encoded frame at the seam that could stutter, duplicate, or
    freeze, just each clip's own frames back to back.
    """
    if not clips:
        raise ValueError("concat_video_clips: no clips to concatenate")
    if len(clips) == 1:
        return clips[0]

    timeout_s = max(60.0, len(clips) * 15.0)
    with tempfile.TemporaryDirectory(prefix="vo_broll_concat_") as tmp:
        lines = []
        for i, clip_bytes in enumerate(clips):
            clip_path = os.path.join(tmp, f"seg_{i}.mp4")
            with open(clip_path, "wb") as fh:
                fh.write(clip_bytes)
            lines.append(f"file '{clip_path}'")
        list_path = os.path.join(tmp, "list.txt")
        with open(list_path, "w") as fh:
            fh.write("\n".join(lines))

        out_path = os.path.join(tmp, "out.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        await _run_ffmpeg(cmd, timeout_s=timeout_s, what="broll_sequence_concat")
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
