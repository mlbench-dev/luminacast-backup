import os
import re
import math
import tempfile
import subprocess
import requests
import logging

try:
    import sentry_sdk
except ImportError:  # pragma: no cover — keeps the module importable in test envs
    class _NullSentry:
        @staticmethod
        def capture_exception(_e=None):
            return None
    sentry_sdk = _NullSentry()

logger = logging.getLogger(__name__)

def _env_first(*names: str, default: str = "") -> str:
    """Return the first non-empty value found among the given env var names."""
    for n in names:
        v = os.environ.get(n, "")
        if v:
            return v
    return default


R2_ENDPOINT = _env_first("R2_ENDPOINT")
# Orchestrator stack uses *_ID / *_ACCESS_KEY; legacy HOSTKEY worker used the short names.
R2_ACCESS_KEY = _env_first("R2_ACCESS_KEY_ID", "R2_ACCESS_KEY")
R2_SECRET_KEY = _env_first("R2_SECRET_ACCESS_KEY", "R2_SECRET_KEY")
R2_BUCKET = _env_first("R2_BUCKET", default="luminacast")
R2_PUBLIC_URL = _env_first("R2_PUBLIC_URL", default="https://media.luminacast.com")


def _get_s3():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
    )


def _escape_ffmpeg_text(text: str) -> str:
    """Escape for FFmpeg filtergraph: first escape the filter-graph metacharacters,
    then escape for single-quoted drawtext text.
    """
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = text.replace("%", "\\%")
    text = text.replace("'", r"\'")
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace(";", "\\;")
    return text


def _timeline_duration(timeline: dict) -> float:
    """Max(e) across every element in every track — the canvas length."""
    longest = 0.0
    for track in (timeline or {}).get("tracks") or []:
        for el in (track or {}).get("elements") or []:
            try:
                e = float(el.get("e") or 0)
            except (TypeError, ValueError) as ex:
                sentry_sdk.capture_exception(ex)
                continue
            if e > longest:
                longest = e
    return longest


def _canvas_size(timeline: dict, fallback=(480, 848)) -> tuple[int, int]:
    """Render resolution. Prefer timeline.compositionWidth/Height; else fallback."""
    tl = timeline or {}
    try:
        cw = int(tl.get("compositionWidth") or fallback[0])
    except (TypeError, ValueError) as ex:
        sentry_sdk.capture_exception(ex)
        cw = fallback[0]
    try:
        ch = int(tl.get("compositionHeight") or fallback[1])
    except (TypeError, ValueError) as ex:
        sentry_sdk.capture_exception(ex)
        ch = fallback[1]
    # ensure even dims for yuv420p
    if cw % 2:
        cw += 1
    if ch % 2:
        ch += 1
    return cw, ch


def _canvas_fps(timeline: dict, fallback: int = 30) -> int:
    try:
        return int((timeline or {}).get("fps") or fallback)
    except (TypeError, ValueError) as ex:
        sentry_sdk.capture_exception(ex)
        return fallback


def _derive_tracks_from_timeline(timeline: dict, baked_urls: dict) -> tuple[list, list]:
    """Back-compat: when the orchestrator hasn't sent compose_video_tracks /
    compose_audio_tracks yet, derive them from the timeline + baked_urls.

    Returns (video_tracks, audio_tracks) where each entry is a dict with
    keys: url, s, e, block_id.
    """
    video_tracks: list[dict] = []
    audio_tracks: list[dict] = []
    for track in (timeline or {}).get("tracks") or []:
        for el in (track or {}).get("elements") or []:
            meta = el.get("metadata") or {}
            if not meta.get("bonded"):
                continue
            block_id = meta.get("block_id") or ""
            try:
                s = float(el.get("s") or 0)
                e = float(el.get("e") or 0)
            except (TypeError, ValueError) as ex:
                sentry_sdk.capture_exception(ex)
                continue
            if e <= s:
                continue
            eid = el.get("id") or ""
            # V1 (snapshot/baked clip): has paired_audio_element_id
            if meta.get("paired_audio_element_id") and eid in baked_urls:
                _url = baked_urls[eid]
                # Belt-and-suspenders: if a face_ref.jpg ever leaks into
                # baked_urls (old orchestrator deployments substituted it
                # for voiceover-only blocks), drop it here — the slot will
                # remain canvas, the paired audio still mixes in.
                if _looks_like_image_url(_url):
                    logger.info(
                        "compose: skipping image-only V1 src for block=%s slot=%.2f..%.2f (%s)",
                        block_id, s, e, _url[:80],
                    )
                    continue
                video_tracks.append({
                    "url": _url, "s": s, "e": e, "block_id": block_id,
                })
            # A1 (voiceover audio): has paired_video_element_id, src in props
            elif meta.get("paired_video_element_id"):
                src = (el.get("props") or {}).get("src") or ""
                if src:
                    audio_tracks.append({
                        "url": src, "s": s, "e": e, "block_id": block_id,
                    })
    return video_tracks, audio_tracks


def _download(url: str, dest: str, timeout: float) -> int:
    resp = requests.get(url, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)
    return len(resp.content)


def _run(cmd: list[str], timeout: float, label: str) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or "")[-1800:]
        logger.error("ffmpeg step %s failed (rc=%s): %s", label, r.returncode, tail)
        raise RuntimeError(f"ffmpeg {label} failed (rc={r.returncode}): {tail[-800:]}")
    return r


# A composed timeline should never show black between blocks. blackdetect on
# the final mp4 must report no black run longer than this at an internal
# position. Head/tail are tolerated up to _BLACK_EDGE_TOLERANCE_S because a
# clip may legitimately fade up from / down to black at the very edges.
_MAX_INTERNAL_BLACK_S = 0.15
_BLACK_EDGE_TOLERANCE_S = 0.05


def _detect_internal_black_runs(
    path: str,
    duration_s: float,
    *,
    max_black_s: float = _MAX_INTERNAL_BLACK_S,
    edge_tolerance_s: float = _BLACK_EDGE_TOLERANCE_S,
    timeout: float = 120.0,
) -> list[tuple[float, float]]:
    """Run ffmpeg blackdetect on ``path`` and return internal black runs.

    Returns a list of ``(start, end)`` black intervals longer than
    ``max_black_s`` that fall inside the body of the video — i.e. excluding a
    lead-in within ``edge_tolerance_s`` of t=0 and a tail within
    ``edge_tolerance_s`` of ``duration_s``. An empty list means the composite
    has no offending boundary blacks.
    """
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", path,
                "-vf", f"blackdetect=d={max_black_s:.3f}:pix_th=0.10",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return []
    runs: list[tuple[float, float]] = []
    for m in re.finditer(
        r"black_start:([0-9.]+)\s+black_end:([0-9.]+)", r.stderr or ""
    ):
        try:
            start = float(m.group(1))
            end = float(m.group(2))
        except ValueError as e:
            sentry_sdk.capture_exception(e)
            continue
        if (end - start) <= max_black_s:
            continue
        # Skip a legitimate lead-in / tail at the extreme edges.
        if start <= edge_tolerance_s:
            continue
        if duration_s > 0 and end >= (duration_s - edge_tolerance_s):
            continue
        runs.append((start, end))
    return runs


def _probe_streams(path: str, probe_timeout: float) -> tuple[bool, bool]:
    """Return (has_video, has_audio) for a media file.

    Defensive against image inputs that masquerade as video tracks — those
    surface as has_video=True but has_audio=False, and any filter graph that
    references [N:a] for such an input fails with "matches no streams".
    Returns (True, True) on probe failure so the caller falls through to
    the existing error handling rather than silently dropping the input.
    """
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries",
                "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=probe_timeout,
        )
    except Exception as ex:
        sentry_sdk.capture_exception(ex)
        return True, True
    out = (r.stdout or "").lower()
    has_video = "video" in out
    has_audio = "audio" in out
    return has_video, has_audio


_PINGPONG_MAX_SEGMENTS_WORKER = 6


def _probe_duration_s(path: str, timeout: float = 15.0) -> float:
    """Return the container duration of ``path`` in seconds. 0 if unknown."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "0").strip() or 0.0)
    except (subprocess.TimeoutExpired, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return 0.0


def _pingpong_video_filter(
    *,
    src_dur: float,
    target_dur: float,
    in_label: str,
    out_label: str,
) -> str:
    """Worker-local mirror of services.block_normalize.build_pingpong_video_filter.

    Builds an ffmpeg filter chain that extends ``in_label`` to ``target_dur``
    by ping-pong looping a clip of length ``src_dur`` (forward → reverse →
    forward …). Replaces the older ``tpad=stop_mode=clone`` path which left
    visible "freezes" at slot boundaries.
    """
    if src_dur <= 0 or target_dur <= 0:
        raise ValueError(
            f"_pingpong_video_filter: invalid src_dur={src_dur} target_dur={target_dur}"
        )
    src_name = in_label.strip("[]")
    out_name = out_label.strip("[]")
    if target_dur <= src_dur + 1e-3:
        return (
            f"[{src_name}]trim=duration={target_dur:.3f},"
            f"setpts=PTS-STARTPTS[{out_name}]"
        )
    segments_needed = max(1, math.ceil(target_dur / src_dur))
    capped = min(segments_needed, _PINGPONG_MAX_SEGMENTS_WORKER)
    split_targets = "".join(f"[vseg{i}]" for i in range(capped))
    parts = [f"[{src_name}]split={capped}{split_targets}"]
    for i in range(capped):
        if i % 2 == 0:
            parts.append(
                f"[vseg{i}]trim=duration={src_dur:.3f},setpts=PTS-STARTPTS[vp{i}]"
            )
        else:
            parts.append(
                f"[vseg{i}]trim=duration={src_dur:.3f},setpts=PTS-STARTPTS,"
                f"reverse[vp{i}]"
            )
    concat_inputs = "".join(f"[vp{i}]" for i in range(capped))
    parts.append(f"{concat_inputs}concat=n={capped}:v=1:a=0[vcat]")
    raw_loop_dur = capped * src_dur
    if raw_loop_dur >= target_dur - 1e-3:
        parts.append(
            f"[vcat]trim=duration={target_dur:.3f},setpts=PTS-STARTPTS[{out_name}]"
        )
    else:
        stretch = target_dur / raw_loop_dur
        parts.append(
            f"[vcat]setpts=PTS*{stretch:.6f},"
            f"trim=duration={target_dur:.3f},setpts=PTS-STARTPTS[{out_name}]"
        )
    return ";".join(parts)


def _looks_like_image_url(url: str) -> bool:
    """Cheap pre-download filter for obviously-image URLs.

    The orchestrator should never put face_ref.jpg / .png / .webp into a
    video-track slot, but historical deployments did. Catching it on the URL
    avoids a wasted download + ffprobe cycle. Probe is still the authority.
    """
    if not url:
        return False
    low = url.split("?", 1)[0].lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        if low.endswith(ext):
            return True
    return False


def _build_overlay_filter_parts(overlay_elements, current_label):
    """Build the filtergraph parts that composite image/video overlays.

    Returns ``(filter_parts, current_label)`` where ``current_label`` is the
    output pad after the last overlay. Each overlay element must already carry
    an ``_input_index`` assigned during download. Pulled out of the compose
    routine so the constructed filter chain can be unit-tested without invoking
    FFmpeg.

    Video overlays (stock track) are opaque and carry their own PTS, so they
    are scaled to geometry, time-shifted into their absolute start via
    ``setpts`` (NOT ``tpad``, whose prepended frames rendered as opaque black on
    the production FFmpeg build), then overlaid with an enable window.
    ``eof_action=pass`` guards clips shorter than their slot. Image overlays
    keep the alpha-aware ``format=yuva420p`` + ``tpad`` path unchanged.
    """
    filter_parts = []
    for ov in overlay_elements:
        if "_input_index" not in ov:
            continue
        idx = ov["_input_index"]
        ov_type = ov.get("type", "image")
        try:
            x = int(ov.get("x", 0))
            y = int(ov.get("y", 0))
            start = float(ov.get("start_s", 0))
            end = float(ov.get("end_s", 9999))
        except (TypeError, ValueError) as ex:
            sentry_sdk.capture_exception(ex)
            continue
        w = ov.get("width")
        h = ov.get("height")

        scale_label = f"[scaled{idx}]"
        fade_in_s = float(ov.get("fadeInDurationInSeconds") or 0)
        fade_out_s = float(ov.get("fadeOutDurationInSeconds") or 0)

        fade_chain = ""
        if fade_in_s > 0:
            fade_chain += f",fade=t=in:st={start}:d={fade_in_s}:alpha=1"
        if fade_out_s > 0:
            fade_out_start = max(float(start), float(end) - fade_out_s)
            fade_chain += f",fade=t=out:st={fade_out_start}:d={fade_out_s}:alpha=1"
        alpha_prefix = "format=yuva420p,"
        fit = ov.get("fit")
        if w and h and int(w) > 0 and int(h) > 0:
            bw, bh = int(w), int(h)
            fit_str = str(fit).lower() if fit else ""
            if fit_str == "contain":
                if ov_type == "video":
                    scale_expr = (
                        f"format=rgba,scale={bw}:{bh}:force_original_aspect_ratio=decrease,"
                        f"pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1"
                    )
                else:
                    scale_expr = (
                        f"scale={bw}:{bh}:force_original_aspect_ratio=decrease,"
                        f"pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1"
                    )
            elif fit_str == "cover":
                scale_expr = (
                    f"scale={bw}:{bh}:force_original_aspect_ratio=increase,"
                    f"crop={bw}:{bh},setsar=1"
                )
            else:
                scale_expr = f"scale={bw}:{bh}"
        else:
            scale_expr = "scale=iw*0.3:-1"
            if x == 0 and y == 0:
                x = -1
                y = -1

        tpad_part = (
            f",tpad=start_duration={start}:start_mode=add:color=0x00000000"
            if start > 0 else ""
        )
        out_label = f"[img{idx}]"
        ox = "W-w-20" if x == -1 else str(int(x))
        oy = "H-h-20" if y == -1 else str(int(y))
        if ov_type == "video":
            filter_parts.append(
                f"[{idx}:v]{scale_expr},setpts=PTS-STARTPTS+{start}/TB"
                f"{scale_label}"
            )
            filter_parts.append(
                f"{current_label}{scale_label}overlay={ox}:{oy}"
                f":enable='between(t,{start},{end})':eof_action=pass{out_label}"
            )
        else:
            filter_parts.append(
                f"[{idx}:v]{alpha_prefix}{scale_expr}{tpad_part}"
                f"{fade_chain}{scale_label}"
            )
            enable_expr = f"between(t\\,{start}\\,{end})"
            filter_parts.append(
                f"{current_label}{scale_label}overlay={ox}:{oy}"
                f":enable='{enable_expr}'{out_label}"
            )
        current_label = out_label

    return filter_parts, current_label


def _run_ffmpeg_compose(req):
    """Slot-aligned canvas assembly.

    Replaces the previous concat-based pipeline. The previous approach laid
    baked clips end-to-end on a "real time" base, accumulating drift whenever
    a bake's duration disagreed with its timeline slot (e - s) — and ignoring
    voiceover-only blocks entirely on the video side, so the base video ran
    short of the timeline's audio. The visible failure mode was a frozen
    last-frame tail while audio kept playing.

    This version:
      1. Builds a black canvas at timeline_duration = max(e) across all elements.
      2. For each baked V1 clip, trims/pads to exactly (e - s) and overlays on
         the canvas with PTS shifted to s so it lands at its slot.
      3. Mixes all per-block audio tracks via adelay+amix at their s offsets.
         Voiceover-only blocks contribute audio only.
      4. Lets the existing overlay/caption pipeline run on top of this canvas.

    The render request may carry the new fields ``compose_video_tracks`` and
    ``compose_audio_tracks`` (each a list of ``{url, s, e, block_id}``). When
    absent we fall back to deriving them from ``timeline`` + ``baked_urls``,
    so this stays compatible with in-flight orchestrator deployments.
    """
    render_id = req.render_id
    baked_urls = getattr(req, "baked_urls", None) or {}
    timeline = req.timeline
    overlay_elements = getattr(req, "overlay_elements", None) or []

    video_tracks = list(getattr(req, "compose_video_tracks", None) or [])
    audio_tracks = list(getattr(req, "compose_audio_tracks", None) or [])
    if not video_tracks and not audio_tracks:
        # Legacy clients: derive from timeline + baked_urls.
        video_tracks, audio_tracks = _derive_tracks_from_timeline(timeline, baked_urls)

    timeline_dur = _timeline_duration(timeline)
    if timeline_dur <= 0 and (video_tracks or audio_tracks):
        # Fall back to longest track end.
        for t in list(video_tracks) + list(audio_tracks):
            try:
                e = float(t.get("e") or 0)
            except (TypeError, ValueError) as ex:
                sentry_sdk.capture_exception(ex)
                continue
            if e > timeline_dur:
                timeline_dur = e

    if timeline_dur <= 0:
        raise RuntimeError("Cannot compose: timeline duration is zero or unknown")

    canvas_w, canvas_h = _canvas_size(timeline)
    canvas_fps = _canvas_fps(timeline)

    # Per-block download / normalize timeouts scale with each block's own
    # slot length, never a fixed cap. The final-compose timeout scales with
    # the full canvas length. Floor of 60s keeps tiny casts comfortable.
    def block_timeout(seconds: float) -> float:
        return max(60.0, 6.0 * float(seconds or 0))

    final_timeout = max(120.0, 8.0 * float(timeline_dur))

    with tempfile.TemporaryDirectory(prefix=f"compose_{render_id}_") as tmpdir:
        # ── 1. Normalize each baked video clip to its exact slot length. ──
        # We re-encode to a uniform fps + even dims, then -t to clamp to
        # (e - s) and apad to extend short bakes with silence. The shift
        # to the canvas timeline happens in the filter graph below via
        # setpts; we do NOT bake PTS shifts into the file itself because
        # downstream overlay filtering re-references the streams from t=0.
        normalized_videos: list[dict] = []  # {path, s, e, block_id, dur}
        for vt in sorted(video_tracks, key=lambda t: float(t.get("s") or 0)):
            url = vt.get("url") or ""
            try:
                s = float(vt.get("s") or 0)
                e = float(vt.get("e") or 0)
            except (TypeError, ValueError) as ex:
                sentry_sdk.capture_exception(ex)
                continue
            slot_dur = max(0.0, e - s)
            if not url or slot_dur <= 0:
                continue
            block_id = vt.get("block_id") or ""

            # Cheap pre-download skip: face_ref.jpg-style URLs are not avatar
            # clips and must never appear in the V1 base track. The slot stays
            # canvas; the paired voiceover audio still mixes in via the audio
            # pass below.
            if _looks_like_image_url(url):
                logger.info(
                    "compose %s: skipping image-only V1 src for block=%s "
                    "(%s) — canvas remains visible for this slot",
                    render_id, block_id, url[:80],
                )
                continue

            raw = os.path.join(tmpdir, f"raw_v_{block_id or len(normalized_videos)}.mp4")
            try:
                size = _download(url, raw, timeout=block_timeout(slot_dur))
                logger.info(
                    "compose %s: dl video block=%s slot=%.2fs (%d bytes)",
                    render_id, block_id, slot_dur, size,
                )
            except Exception as ex:
                sentry_sdk.capture_exception(ex)
                logger.warning(
                    "compose %s: failed to download video block %s (%s); "
                    "leaving the slot as canvas background",
                    render_id, block_id, ex,
                )
                continue

            # Authoritative probe: drop inputs that have no video stream
            # (image files whose URL didn't end in a recognized extension)
            # so we never feed them to the overlay graph.
            has_v, _ = _probe_streams(raw, probe_timeout=block_timeout(slot_dur))
            if not has_v:
                logger.info(
                    "compose %s: block=%s downloaded source has no video stream; "
                    "leaving slot as canvas background",
                    render_id, block_id,
                )
                continue

            norm = os.path.join(tmpdir, f"norm_v_{block_id or len(normalized_videos)}.mp4")
            src_dur = _probe_duration_s(raw)
            if src_dur <= 0:
                src_dur = slot_dur  # trim-only fallback when probe fails
            v_prep = (
                f"[0:v]fps={canvas_fps},"
                f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[vprep]"
            )
            v_extend = _pingpong_video_filter(
                src_dur=max(src_dur, 1.0 / float(canvas_fps)),
                target_dur=slot_dur,
                in_label="[vprep]",
                out_label="[vout]",
            )
            cmd = [
                "ffmpeg", "-y", "-loglevel", "warning", "-i", raw,
                "-filter_complex", f"{v_prep};{v_extend}",
                "-map", "[vout]",
                "-r", str(canvas_fps),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-pix_fmt", "yuv420p",
                "-an",
                norm,
            ]
            extension_s = max(0.0, slot_dur - src_dur)
            if extension_s > 0.05:
                logger.info(
                    "compose %s: block=%s ping-pong extension %.2fs "
                    "(src=%.2fs slot=%.2fs)",
                    render_id, block_id, extension_s, src_dur, slot_dur,
                )
            try:
                _run(cmd, timeout=block_timeout(slot_dur), label=f"normalize_{block_id}")
            except Exception as ex:
                sentry_sdk.capture_exception(ex)
                logger.warning(
                    "compose %s: normalize failed for block %s (%s); "
                    "skipping its video — canvas will show through",
                    render_id, block_id, ex,
                )
                continue
            normalized_videos.append({
                "path": norm, "s": s, "e": e,
                "block_id": block_id, "dur": slot_dur,
            })

        # ── 2. Normalize each audio track to its slot. ──
        # adelay applies the s offset later in the audio mix; here we just
        # trim/pad to exactly slot_dur so the mix at s..e is bounded.
        normalized_audios: list[dict] = []  # {path, s, e, block_id, dur}
        for at in sorted(audio_tracks, key=lambda t: float(t.get("s") or 0)):
            url = at.get("url") or ""
            try:
                s = float(at.get("s") or 0)
                e = float(at.get("e") or 0)
            except (TypeError, ValueError) as ex:
                sentry_sdk.capture_exception(ex)
                continue
            slot_dur = max(0.0, e - s)
            if not url or slot_dur <= 0:
                continue
            block_id = at.get("block_id") or ""

            # Cheap pre-download skip: an image URL ending up on the audio
            # side is the same orchestrator bug as on the video side. The
            # mix simply ignores it.
            if _looks_like_image_url(url):
                logger.info(
                    "compose %s: skipping image URL on audio side block=%s (%s)",
                    render_id, block_id, url[:80],
                )
                continue

            raw = os.path.join(tmpdir, f"raw_a_{block_id or len(normalized_audios)}")
            try:
                size = _download(url, raw, timeout=block_timeout(slot_dur))
                logger.info(
                    "compose %s: dl audio block=%s slot=%.2fs (%d bytes)",
                    render_id, block_id, slot_dur, size,
                )
            except Exception as ex:
                sentry_sdk.capture_exception(ex)
                logger.warning(
                    "compose %s: failed to download audio block %s (%s); "
                    "skipping",
                    render_id, block_id, ex,
                )
                continue

            # Probe: any source with no audio stream is dropped before the
            # normalize step, which would otherwise emit "matches no streams"
            # for an [N:a]... filter chain referencing it.
            _, has_a = _probe_streams(raw, probe_timeout=block_timeout(slot_dur))
            if not has_a:
                logger.info(
                    "compose %s: audio block=%s has no audio stream; "
                    "dropping from mix",
                    render_id, block_id,
                )
                continue

            norm = os.path.join(tmpdir, f"norm_a_{block_id or len(normalized_audios)}.m4a")
            cmd = [
                "ffmpeg", "-y", "-loglevel", "warning", "-i", raw,
                "-af", "aresample=48000,aformat=channel_layouts=stereo,apad",
                "-t", f"{slot_dur:.3f}",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                norm,
            ]
            try:
                _run(cmd, timeout=block_timeout(slot_dur), label=f"audio_norm_{block_id}")
            except Exception as ex:
                sentry_sdk.capture_exception(ex)
                logger.warning(
                    "compose %s: audio normalize failed for block %s (%s); "
                    "dropping its audio from the mix",
                    render_id, block_id, ex,
                )
                continue
            normalized_audios.append({
                "path": norm, "s": s, "e": e,
                "block_id": block_id, "dur": slot_dur,
            })

        if not normalized_videos and not normalized_audios:
            raise RuntimeError(
                "compose: no usable video or audio tracks after normalize"
            )

        # ── 3. Build the slot-aligned base (video + mixed audio). ──
        # Inputs: [0] = color canvas, [1..Nv] = normalized videos, then audio.
        base_path = os.path.join(tmpdir, "base.mp4")
        inputs: list[str] = [
            "-f", "lavfi", "-t", f"{timeline_dur:.3f}",
            "-i", f"color=c=black:s={canvas_w}x{canvas_h}:r={canvas_fps}",
        ]
        for v in normalized_videos:
            inputs.extend(["-i", v["path"]])
        v_count = len(normalized_videos)
        for a in normalized_audios:
            inputs.extend(["-i", a["path"]])

        # Each clip is gated onto the canvas by an absolute-time window. A
        # clip's own slot is [s, e], but when the next clip starts after this
        # one ends (e[i] < s[i+1]) the black canvas would show through the gap
        # — the 0.4-0.7s boundary blacks we are fixing. To keep cuts tight with
        # no black, every clip's gate is extended to the next clip's start
        # (clamped to the timeline end) and its last frame is held across the
        # gap with tpad=stop_mode=clone. The next clip's overlay then paints
        # over it at its own start, so the hand-off is frame-tight.
        filter_parts: list[str] = []
        current = "[0:v]"
        n_videos = len(normalized_videos)
        for i, v in enumerate(normalized_videos):
            in_idx = i + 1
            pre_label = f"v{i}p"
            out_label = f"v{i}o"
            if i + 1 < n_videos:
                gate_end = min(float(normalized_videos[i + 1]["s"]), timeline_dur)
            else:
                gate_end = timeline_dur
            gate_end = max(gate_end, float(v["e"]))
            hold_s = max(0.0, gate_end - float(v["e"]))
            # tpad holds the final frame for `hold_s` (applied while the clip
            # is still zero-based, so the cloned frames stay contiguous), then
            # setpts shifts the whole extended stream to land at canvas time s.
            # The held frame covers the gap to the next clip instead of black.
            hold_part = (
                f"tpad=stop_mode=clone:stop_duration={hold_s:.3f},"
                if hold_s > 1e-3 else ""
            )
            filter_parts.append(
                f"[{in_idx}:v]{hold_part}setpts=PTS-STARTPTS+{v['s']:.3f}/TB[{pre_label}]"
            )
            filter_parts.append(
                f"{current}[{pre_label}]overlay=0:0:eof_action=pass:"
                f"enable='between(t,{v['s']:.3f},{gate_end:.3f})'[{out_label}]"
            )
            current = f"[{out_label}]"
        base_v_label = current

        # Audio: adelay each normalized track to its s offset and amix.
        a_input_start = 1 + v_count
        if normalized_audios:
            mix_inputs = []
            for j, a in enumerate(normalized_audios):
                in_idx = a_input_start + j
                delay_ms = max(0, int(round(a["s"] * 1000)))
                label = f"a{j}d"
                filter_parts.append(
                    f"[{in_idx}:a]adelay={delay_ms}|{delay_ms},apad[{label}]"
                )
                mix_inputs.append(f"[{label}]")
            mix_in = "".join(mix_inputs)
            filter_parts.append(
                f"{mix_in}amix=inputs={len(mix_inputs)}:"
                f"duration=longest:dropout_transition=0,"
                f"dynaudnorm=p=0.95[base_a]"
            )
            base_a_label = "[base_a]"
            has_audio = True
        else:
            base_a_label = ""
            has_audio = False

        base_cmd = ["ffmpeg", "-y", "-loglevel", "warning"] + inputs + [
            "-filter_complex", ";".join(filter_parts),
            "-map", base_v_label,
        ]
        if has_audio:
            base_cmd += ["-map", base_a_label]
        base_cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-t", f"{timeline_dur:.3f}",
        ]
        if has_audio:
            base_cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        base_cmd += [base_path]

        try:
            _run(base_cmd, timeout=final_timeout, label="base_assemble")
        except Exception as ex:
            sentry_sdk.capture_exception(ex)
            raise

        try:
            actual = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", base_path],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
            logger.info(
                "compose %s: base assembled, expected=%.2fs, actual=%ss, "
                "videos=%d, audios=%d",
                render_id, timeline_dur, actual,
                len(normalized_videos), len(normalized_audios),
            )
        except Exception as ex:
            sentry_sdk.capture_exception(ex)

        # ── 4. Download overlays (images / video overlays). ──
        # media_inputs tracks (path, kind, duration, start_s) so we can add
        # proper -loop/-t flags and shift their PTS into their visibility
        # window via tpad on the filtergraph.
        media_inputs = []
        for i, ov in enumerate(overlay_elements):
            ov_type = ov.get("type", "")
            try:
                start_s = float(ov.get("start_s", 0) or 0)
                end_s = float(ov.get("end_s", 0) or 0)
            except (TypeError, ValueError) as ex:
                sentry_sdk.capture_exception(ex)
                continue
            duration = max(end_s - start_s, 0.1)
            ov_timeout = block_timeout(duration)
            if ov_type in ("image", "overlay", "sticker", "logo", "gif") and ov.get("src"):
                img_path = os.path.join(tmpdir, f"overlay_{i}.png")
                try:
                    size = _download(ov["src"], img_path, timeout=ov_timeout)
                    ov["_local_path"] = img_path
                    ov["_input_index"] = len(media_inputs) + 1
                    media_inputs.append({
                        "path": img_path, "kind": "image",
                        "duration": duration, "start_s": start_s,
                    })
                    logger.info(
                        "compose %s: overlay img %d (%d bytes, %.2fs @ %.2fs)",
                        render_id, i, size, duration, start_s,
                    )
                except Exception as ex:
                    sentry_sdk.capture_exception(ex)
                    logger.warning(
                        "compose %s: overlay img %d fetch failed: %s",
                        render_id, i, ex,
                    )
            elif ov_type == "video" and ov.get("src"):
                vid_path = os.path.join(tmpdir, f"overlay_vid_{i}.mp4")
                try:
                    size = _download(ov["src"], vid_path, timeout=ov_timeout)
                    ov["_local_path"] = vid_path
                    ov["_input_index"] = len(media_inputs) + 1
                    media_inputs.append({
                        "path": vid_path, "kind": "video",
                        "duration": duration, "start_s": start_s,
                    })
                    logger.info(
                        "compose %s: overlay vid %d (%d bytes, %.2fs @ %.2fs)",
                        render_id, i, size, duration, start_s,
                    )
                except Exception as ex:
                    sentry_sdk.capture_exception(ex)
                    logger.warning(
                        "compose %s: overlay vid %d fetch failed: %s",
                        render_id, i, ex,
                    )

        image_inputs = [m["path"] for m in media_inputs]

        # ── 5. Build the final FFmpeg filter graph: overlays + captions. ──
        filter_parts = []
        inputs = ["-i", base_path]
        for m in media_inputs:
            if m["kind"] == "image":
                inputs.extend([
                    "-loop", "1", "-t", f"{m['duration']:.3f}",
                    "-i", m["path"],
                ])
            else:
                inputs.extend(["-i", m["path"]])

        current_label = "[0:v]"

        caption_overlays = [
            ov for ov in overlay_elements if ov.get("type") in ("caption", "text")
        ]
        srt_path = None
        if caption_overlays:
            srt_path = os.path.join(tmpdir, "captions.srt")
            sorted_caps = sorted(caption_overlays, key=lambda c: c.get("start_s", 0))
            with open(srt_path, "w", encoding="utf-8") as srt_file:
                for i, cap in enumerate(sorted_caps, 1):
                    text = (cap.get("text") or "").strip()
                    if not text:
                        continue
                    try:
                        start = float(cap.get("start_s", 0))
                        end = float(cap.get("end_s", start + 1))
                    except (TypeError, ValueError) as ex:
                        sentry_sdk.capture_exception(ex)
                        continue

                    def fmt(t):
                        h = int(t // 3600)
                        m = int((t % 3600) // 60)
                        s = int(t % 60)
                        ms = int((t - int(t)) * 1000)
                        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

                    srt_file.write(f"{i}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n")
            logger.info(
                "compose %s: wrote %d SRT entries to %s",
                render_id, len(sorted_caps), srt_path,
            )

        # Image and video overlays
        overlay_parts, current_label = _build_overlay_filter_parts(
            overlay_elements, current_label
        )
        filter_parts.extend(overlay_parts)

        if srt_path and os.path.exists(srt_path):
            srt_path_escaped = srt_path.replace(":", r"\:").replace("'", r"\\'")
            sub_label = "[subtitled]"
            filter_parts.append(
                f"{current_label}subtitles={srt_path_escaped}"
                f":force_style='FontName=DejaVu Sans,FontSize=10,"
                f"PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
                f"BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=30'"
                f"{sub_label}"
            )
            current_label = sub_label
            logger.info("compose %s: added SRT subtitles filter", render_id)

        # ── 6. Run final FFmpeg. ──
        output_path = os.path.join(tmpdir, "final.mp4")

        # EBU R128 loudness normalisation on the final encode — broadcast
        # podcast target. The per-block bake already normalised, so this
        # single-pass loudnorm is a safety net that pins the composed
        # timeline at -16 LUFS regardless of per-clip source variation.
        loudnorm_af = "loudnorm=I=-16:TP=-1.5:LRA=11"
        if filter_parts:
            filter_complex = ";".join(filter_parts)
            cmd = ["ffmpeg", "-y", "-loglevel", "warning"] + inputs + [
                "-filter_complex", filter_complex,
                "-map", current_label,
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-af", loudnorm_af,
                "-movflags", "+faststart",
                "-t", f"{timeline_dur:.3f}",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "warning", "-i", base_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-af", loudnorm_af,
                "-movflags", "+faststart",
                "-t", f"{timeline_dur:.3f}",
                output_path,
            ]

        logger.info(
            "compose %s: final assembly — videos=%d, captions=%d, overlays=%d, "
            "canvas_dur=%.2fs",
            render_id, len(normalized_videos), len(caption_overlays),
            len(image_inputs), timeline_dur,
        )

        try:
            _run(cmd, timeout=final_timeout, label="final_assemble")
        except Exception as ex:
            sentry_sdk.capture_exception(ex)
            raise

        output_size = os.path.getsize(output_path)
        logger.info("compose %s: final output %d bytes", render_id, output_size)

        # ── 6b. Verify no boundary blacks. ──
        # The composite must not show black between blocks. Detect any internal
        # black run longer than the threshold and report it (Sentry + log) so a
        # regression in slot gating surfaces instead of shipping silently.
        try:
            black_runs = _detect_internal_black_runs(output_path, timeline_dur)
            if black_runs:
                detail = ", ".join(
                    f"{s:.2f}-{e:.2f}s" for s, e in black_runs
                )
                msg = (
                    f"compose {render_id}: internal black runs > "
                    f"{_MAX_INTERNAL_BLACK_S:.2f}s detected at {detail}"
                )
                logger.error(msg)
                sentry_sdk.capture_exception(RuntimeError(msg))
            else:
                logger.info(
                    "compose %s: blackdetect clean — no internal black runs > %.2fs",
                    render_id, _MAX_INTERNAL_BLACK_S,
                )
        except Exception as ex:
            sentry_sdk.capture_exception(ex)

        # ── 7. Upload to R2. ──
        output_r2_key = f"renders/{render_id}/final.mp4"
        try:
            s3 = _get_s3()
            s3.upload_file(
                output_path, R2_BUCKET, output_r2_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
        except Exception as ex:
            sentry_sdk.capture_exception(ex)
            raise
        logger.info("compose %s: uploaded %s", render_id, output_r2_key)

        return {
            "output_r2_key": output_r2_key,
            "url": f"{R2_PUBLIC_URL}/{output_r2_key}",
            "size_bytes": output_size,
        }
