"""Post-bake block normalization.

After a block clip is baked by ANY provider (HOSTKEY InfiniteTalk, fal
MuseTalk / Wan, WaveSpeed, Modal, RunPod…) the resulting MP4 may not
match the cast's canvas: a cloud provider can return a portrait 720x1280
clip when the cast canvas is landscape 1114x828, or a 25 fps clip when
the canvas runs at 30 fps, or a clip that's a few hundred ms longer
than its slot. The downstream FFmpeg compose service then either
center-crops mismatched frames (chopping off heads) or runs out of
audio for the trailing block (truncating final.mp4 by the difference).

`normalize_baked_block(...)` is an idempotent ffmpeg pass that:

* crop-cover scales the video to fully fill the cast canvas: the input
  is scaled so its smaller dimension matches the canvas, then the
  overflow on the larger dimension is center-cropped. No black bars —
  landscape sources brought into a portrait canvas (e.g. 1104x816 →
  1080x1920) lose the left/right edges instead of getting >40% of the
  frame covered in letterbox bars.
* forces a constant fps to the canvas fps.
* hard-trims A/V to exactly the slot duration via ``-t``.
* re-encodes to H.264 yuv420p + AAC stereo 48 kHz so every block has
  byte-compatible streams for concat / overlay downstream.

Internal logs may name engines (HOSTKEY, fal, etc.) — the function only
exposes ``ValueError`` / ``RuntimeError`` and lets the caller decide
how to surface to the user. UI / HTTP callers should always say
"AI render" rather than naming the engine.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)


_FFPROBE_TIMEOUT_S = 15.0


def _probe_streams(path: str) -> dict:
    """Return a tiny subset of ffprobe's stream metadata for ``path``.

    Keys: ``has_video``, ``has_audio``, ``width``, ``height``, ``fps``
    (best-effort float), ``duration_s``. Missing fields fall back to
    sentinel defaults so callers don't have to KeyError-guard. ffprobe
    failures raise ``RuntimeError`` so the outer ``except`` chain can
    capture to Sentry.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format",
        path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=_FFPROBE_TIMEOUT_S, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed: rc={result.returncode} "
            f"stderr={result.stderr[-500:]}"
        )
    info = json.loads(result.stdout or "{}")
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    out = {
        "has_video": False,
        "has_audio": False,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "duration_s": 0.0,
    }
    for s in streams:
        codec_type = s.get("codec_type")
        if codec_type == "video" and not out["has_video"]:
            out["has_video"] = True
            out["width"] = int(s.get("width") or 0)
            out["height"] = int(s.get("height") or 0)
            # avg_frame_rate is usually "num/den"; r_frame_rate as fallback.
            for fps_key in ("avg_frame_rate", "r_frame_rate"):
                raw = s.get(fps_key) or ""
                if "/" in raw:
                    num, den = raw.split("/", 1)
                    try:
                        num_f = float(num)
                        den_f = float(den)
                        if den_f > 0 and num_f > 0:
                            out["fps"] = num_f / den_f
                            break
                    except ValueError:
                        continue
        elif codec_type == "audio" and not out["has_audio"]:
            out["has_audio"] = True
    try:
        out["duration_s"] = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        out["duration_s"] = 0.0
    return out


def normalize_baked_block(
    *,
    input_bytes: bytes,
    target_width: int,
    target_height: int,
    target_fps: int,
    target_duration_s: float,
    block_id: str,
    render_id: str,
    extend_to_slot: bool = False,
) -> bytes:
    """Conform a freshly-baked block clip to the cast canvas.

    Crop-cover scale onto target_width x target_height: the input is
    scaled so its smaller dimension matches the canvas, then the overflow
    on the larger dimension is center-cropped. The canvas is fully
    filled — no letterbox / pillarbox bars regardless of input aspect.
    Then force constant fps to ``target_fps`` and re-encode H.264 yuv420p
    + AAC stereo 48 kHz so every block has byte-compatible streams.

    Conform-only (Phase 1): this function NEVER changes the clip's
    duration. Output duration == input duration. It does not pad
    (ping-pong / clone / stretch all deleted) and it does not trim to
    slot — duration shaping is owned entirely by
    ``services.block_extension.trim_block_to_slot``, which head-trims the
    deliberately-overshot motion bake down to exactly slot length. If
    normalize trimmed to slot here it would destroy the overshoot surplus
    the head-trim needs.

    ``extend_to_slot`` is retained as a no-op keyword for call-site
    compatibility; it has no effect.

    Raises if normalization fails — caller decides whether to fall back
    to the un-normalized bytes or surface the error.
    """
    if not input_bytes:
        raise ValueError("normalize_baked_block: empty input_bytes")
    if target_width <= 0 or target_height <= 0:
        raise ValueError(
            f"normalize_baked_block: invalid canvas {target_width}x{target_height}"
        )
    if target_fps <= 0:
        raise ValueError(f"normalize_baked_block: invalid fps {target_fps}")
    if target_duration_s <= 0:
        raise ValueError(
            f"normalize_baked_block: invalid duration {target_duration_s}"
        )

    # Snap duration to the nearest whole frame at target_fps so trim doesn't
    # land in the middle of a frame and produce sub-frame jitter at concat.
    snapped_duration_s = round(target_duration_s * target_fps) / float(target_fps)
    if snapped_duration_s <= 0:
        snapped_duration_s = target_duration_s

    # Per-block timeout scales with duration: long blocks legitimately
    # take longer to re-encode. Floor of 60s so tiny clips don't fail
    # on cold caches; no hardcoded ceiling.
    timeout_s = max(60.0, 4.0 * float(target_duration_s))

    tmpdir = tempfile.mkdtemp(prefix=f"normalize_{block_id}_")
    try:
        in_path = os.path.join(tmpdir, "in.mp4")
        out_path = os.path.join(tmpdir, "out.mp4")
        with open(in_path, "wb") as f:
            f.write(input_bytes)

        try:
            probe = _probe_streams(in_path)
        except Exception as probe_exc:
            sentry_sdk.capture_exception(probe_exc)
            # Previously: swallowed and faked a 0x0/0s probe so ffmpeg would
            # "figure it out" — that's what produced a mostly-frozen padded
            # clip several steps downstream, only caught much later by Phase
            # 3 validation with no clue it started here. An unreadable input
            # means the provider returned bytes that aren't a valid video at
            # all (corrupt/incomplete download) — fail loudly, right here.
            raise ValueError(
                f"normalize_baked_block: input video unreadable by ffprobe "
                f"for block {block_id} render {render_id} "
                f"({len(input_bytes)} bytes) — {probe_exc}"
            ) from probe_exc

        in_w = probe["width"] or 0
        in_h = probe["height"] or 0
        in_fps = probe["fps"] or 0.0
        in_dur = probe["duration_s"] or 0.0
        has_audio = bool(probe["has_audio"])

        # Filter graph. Crop-cover: force_original_aspect_ratio=increase
        # scales so the SMALLER input dimension hits the canvas, then
        # crop=W:H center-crops the larger overflowing dimension. This
        # fully fills the canvas — landscape sources brought into a
        # portrait canvas lose left/right edges instead of getting black
        # letterbox bars top/bottom.
        # setsar=1 normalises pixel-aspect-ratio in case the source comes
        # back with non-square pixels (some upstream responses do).
        #
        # Conform-only / duration-preserving: we DO NOT pad or trim here.
        # Padding (ping-pong / clone / stretch) is deleted — it produced
        # the "walks backwards" / "freeze" artefacts. Trimming to slot is
        # owned by ``block_extension.trim_block_to_slot``, which head-trims
        # the overshot motion bake; capping the duration here would destroy
        # the surplus that head-trim needs. So the output keeps the input's
        # own duration (snapped to a whole frame at target_fps).
        v_prep = (
            f"[0:v]fps={target_fps},"
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},"
            f"setsar=1[vprep]"
        )
        # Preserve the bake's own length. Snap to a whole frame so concat
        # downstream never lands mid-frame. ``-t`` is set to the bake
        # duration explicitly because the no-audio branch wires up an
        # *infinite* anullsrc stream — without this cap that stream would
        # run forever.
        if in_dur > 0:
            conform_dur = round(in_dur * target_fps) / float(target_fps)
        else:
            conform_dur = float(snapped_duration_s)
        conform_dur = max(conform_dur, 1.0 / float(target_fps))
        v_filter = f"{v_prep};[vprep]null[v]"
        audio_whole_dur = conform_dur
        t_flag = ["-t", f"{conform_dur:.3f}"]

        if has_audio:
            # Pad audio with silence at the end so the audio stream
            # reaches the canonical length without re-pitching the voice.
            # The lipsync stage downstream masks the mouth over silent
            # tails, so the extension doesn't create a visible glitch.
            a_filter = (
                f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"apad=whole_dur={audio_whole_dur:.3f}[a]"
            )
            filter_complex = f"{v_filter};{a_filter}"
            cmd = [
                "ffmpeg", "-y",
                "-i", in_path,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-ar", "48000", "-ac", "2",
                *t_flag,
                out_path,
            ]
        else:
            # Synthesize a silent stereo 48k track via lavfi so the output
            # always carries an audio stream — concat-copy downstream
            # otherwise drops audio across the entire timeline.
            a_filter = (
                f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"apad=whole_dur={audio_whole_dur:.3f}[a]"
            )
            filter_complex = f"{v_filter};{a_filter}"
            cmd = [
                "ffmpeg", "-y",
                "-i", in_path,
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-ar", "48000", "-ac", "2",
                *t_flag,
                out_path,
            ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as te:
            sentry_sdk.capture_exception(te)
            raise RuntimeError(
                f"normalize ffmpeg timed out after {timeout_s:.0f}s "
                f"for block {block_id} render {render_id}"
            ) from te

        if result.returncode != 0 or not os.path.exists(out_path):
            stderr_tail = (result.stderr or "")[-1500:]
            err = RuntimeError(
                f"normalize ffmpeg failed (rc={result.returncode}) "
                f"for block {block_id}: {stderr_tail}"
            )
            sentry_sdk.capture_exception(err)
            raise err

        with open(out_path, "rb") as f:
            output_bytes = f.read()

        # Probe the output so the log reflects the actual mux duration.
        # Conform-only preserves the bake's own length (no pad / no
        # trim-to-slot), so out_dur ≈ in_dur. Probe failure falls back to
        # 0.0 rather than crashing the log line.
        try:
            out_probe = _probe_streams(out_path)
            out_dur = float(out_probe.get("duration_s") or 0.0)
        except Exception as probe_exc:
            sentry_sdk.capture_exception(probe_exc)
            out_dur = 0.0

        logger.info(
            "[normalize] block %s render %s: in=%dx%d@%.1f %.2fs "
            "→ %dx%d@%d slot=%.2fs out=%.2fs (conform-only, "
            "duration-preserving; %d → %d bytes)",
            block_id, render_id,
            in_w, in_h, in_fps, in_dur,
            target_width, target_height, target_fps, snapped_duration_s, out_dur,
            len(input_bytes), len(output_bytes),
        )
        return output_bytes
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
