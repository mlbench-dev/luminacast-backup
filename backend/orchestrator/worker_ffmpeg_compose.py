import os
import re
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

# Reused from cast_ffmpeg_composer.py rather than reimplemented: font-file
# resolution (fontFamily string → actual bundled TTF path) and drawtext
# text escaping are small, pure, already-tested helpers with no dependency
# back on this module, so importing them keeps the two composers' caption
# fonts/escaping in sync instead of maintaining two copies that can drift.
from services.cast_ffmpeg_composer import (
    _resolve_caption_font_file,
    _caption_safe_width,
    _caption_font,
    _wrap_text_to_width,
    _measure_text_width,
    _chars_per_line,
    _group_caption_tokens_into_pages,
    cap_tokens_to_line_budget,
    _MAX_WORDS_PER_PAGE,
)


_TRUTHY = {"1", "true", "yes", "on"}

# One gentle colour treatment on the FINAL composited picture — before
# captions burn in — so the avatar bake, AI b-roll and Pexels stock (each
# graded differently at source) read as one video instead of three clips
# taped together. Deliberately subtle; over-grading looks worse than none.
# Off by default. Tune the exact look with COMPOSE_COLOR_GRADE_FILTER (any
# valid ffmpeg -vf chain) without a redeploy.
_DEFAULT_COLOR_GRADE = "eq=contrast=1.045:saturation=1.06:gamma=0.985,colorbalance=rm=0.02:bm=-0.02"


def color_grade_filter() -> str:
    """The final-picture grade filter chain, or "" when disabled."""
    if os.environ.get("COMPOSE_COLOR_GRADE_ENABLED", "").strip().lower() not in _TRUTHY:
        return ""
    override = os.environ.get("COMPOSE_COLOR_GRADE_FILTER", "").strip()
    return override or _DEFAULT_COLOR_GRADE


def _drawtext_color(value, default: str) -> str:
    """Convert a caption color to FFmpeg drawtext's accepted format.

    Our stored colors are CSS-style hex (#RRGGBB or #RRGGBBAA, from the
    caption preset table / editor color pickers). drawtext wants either a
    named color or 0xRRGGBB[@alpha]; anything already in a format drawtext
    understands (a bare name, or an existing "black@0.5"-style value like
    the ffmpegBoxColor default) is passed through unchanged.
    """
    if not value:
        return default
    v = str(value).strip()
    if v.startswith("#"):
        hex_part = v[1:]
        if len(hex_part) == 8:
            rgb, aa = hex_part[:6], hex_part[6:]
            try:
                alpha = int(aa, 16) / 255.0
                return f"0x{rgb}@{alpha:.2f}"
            except ValueError:
                return f"0x{rgb}"
        if len(hex_part) == 6:
            return f"0x{hex_part}"
        return default
    return v

def _env_first(*names: str, default: str = "") -> str:
    """Return the first non-empty value found among the given env var names.

    Falls back to config.settings (keyed on the first name) if every env
    var is empty. pydantic-settings loads .env into the Settings object
    only — it never exports those values back into the real process
    os.environ — so a bare os.environ.get() lookup comes back empty even
    when e.g. R2_ENDPOINT is genuinely configured in .env. This silently
    produced an empty R2_ENDPOINT here, which boto3 then rejected with
    "Invalid endpoint: " only at final-video upload time, after the whole
    compose had already run. Same gap, same fix, as
    services/render_providers.py's _wavespeed_api_key().
    """
    for n in names:
        v = os.environ.get(n, "")
        if v:
            return v
    if names:
        try:
            from config import settings
            v = getattr(settings, names[0], "") or ""
            if v:
                return v
        except Exception:
            pass
    return default


# Local-dev escape hatch: some local ffmpeg builds (e.g. Homebrew without
# --enable-libass) lack the `subtitles` filter entirely, which fails the
# final compose step on an otherwise-correct render. Unset/false in
# production — .env.local only, so Docker/prod workers are unaffected.
SKIP_CAPTION_BURN_IN = _env_first("SKIP_CAPTION_BURN_IN").strip().lower() in ("1", "true", "yes")

R2_ENDPOINT = _env_first("R2_ENDPOINT")
# Orchestrator stack uses *_ID / *_ACCESS_KEY; legacy HOSTKEY worker used the short names.
R2_ACCESS_KEY = _env_first("R2_ACCESS_KEY_ID", "R2_ACCESS_KEY")
R2_SECRET_KEY = _env_first("R2_SECRET_ACCESS_KEY", "R2_SECRET_KEY")
R2_BUCKET = _env_first("R2_BUCKET", default="luminacast")
R2_PUBLIC_URL = _env_first("R2_PUBLIC_URL", default="https://media.luminacast.com")

# PR-D: prefer a short reversed-tail bounce over a held last frame when bridging
# an inter-clip boundary gap, so the gap shows subtle motion instead of a
# freeze. Bounded to small gaps; a held frame (tpad) remains the fallback for
# very short or oversized gaps. Mirrors the orchestrator
# services.block_extension ladder.
TAIL_REVERSE_LOOP_ENABLED = (
    os.environ.get("TAIL_REVERSE_LOOP_ENABLED", "1").strip().lower()
    not in ("0", "false", "no", "off")
)
_TAIL_REVERSE_LOOP_MAX_GAP_S = float(
    os.environ.get("TAIL_REVERSE_LOOP_MAX_GAP_SEC", "1.5")
)
_TAIL_REVERSE_SEGMENT_S = float(
    os.environ.get("TAIL_REVERSE_SEGMENT_SEC", "0.5")
)


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


def _env_float_clamped(name: str, default: float, lo: float, hi: float) -> float:
    """Read an env var as a float clamped to [lo, hi], falling back to default."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError) as ex:
        sentry_sdk.capture_exception(ex)
        return default
    return max(lo, min(hi, val))


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean env var. Truthy: 1/true/yes/on (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def phone_mic_filter_enabled() -> bool:
    """Whether to apply the phone-mic lo-fi filter to non-mic-on VO.

    Default ON. Override with ``PHONE_MIC_FILTER_NON_MICON=0``.
    """
    return _env_flag("PHONE_MIC_FILTER_NON_MICON", True)


def block_is_mic_on(track_or_meta) -> bool:
    """True when the given audio track / element-metadata is a mic-on block.

    Mic-on blocks keep the clean studio voice; everything else is eligible for
    the phone-mic filter. Only an explicit truthy ``mic_on`` counts as mic-on —
    unset/``None`` is treated as non-mic-on so the lo-fi texture is the default.
    """
    if isinstance(track_or_meta, dict):
        return track_or_meta.get("mic_on") is True
    return getattr(track_or_meta, "mic_on", None) is True


def phone_mic_filter_chain() -> str:
    """FFmpeg audio-filter chain that makes a clean VO sound phone-recorded.

    Telephone-bandwidth bandpass (HPF + LPF) + mild compression + soft
    saturation, so non-mic-on VO reads as "talking to a phone camera" while
    staying intelligible. Cutoffs and saturation are env-tunable:

      * ``PHONE_MIC_HPF``        high-pass cutoff Hz (default 300)
      * ``PHONE_MIC_LPF``        low-pass cutoff Hz (default 3400)
      * ``PHONE_MIC_SATURATION`` saturation mix 0..1 (default 0.08)
    """
    hpf = _env_float_clamped("PHONE_MIC_HPF", 300.0, 20.0, 2000.0)
    lpf = _env_float_clamped("PHONE_MIC_LPF", 3400.0, 2000.0, 20000.0)
    sat = _env_float_clamped("PHONE_MIC_SATURATION", 0.08, 0.0, 1.0)
    dry = max(0.0, 1.0 - sat)
    return (
        f"highpass=f={hpf:g},lowpass=f={lpf:g},"
        f"acompressor=threshold=0.1:ratio=3:attack=5:release=50,"
        f"aeval='{dry:g}*val(0)+{sat:g}*tanh(3*val(0))'"
    )


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
                video_tracks.append({
                    "url": baked_urls[eid], "s": s, "e": e, "block_id": block_id,
                })
            # A1 (voiceover audio): has paired_video_element_id, src in props
            elif meta.get("paired_video_element_id"):
                src = (el.get("props") or {}).get("src") or ""
                if src:
                    audio_tracks.append({
                        "url": src, "s": s, "e": e, "block_id": block_id,
                        "mic_on": meta.get("mic_on") is True,
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
        if ov.get("_pip_bg"):
            continue  # composited behind a PIP face during normalization
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


def _font_line_height(font, font_size: int) -> float:
    """Line box height (ascent + descent) for a loaded PIL font."""
    if font is not None:
        try:
            ascent, descent = font.getmetrics()
            if ascent + descent > 0:
                return float(ascent + descent)
        except Exception:  # noqa: BLE001 — fall through to the estimate
            pass
    return float(font_size) * 1.3


def _ink_bounds(text: str, font) -> tuple:
    """(top, bottom) pixel offsets of ``text``'s ink from its baseline.

    ``top`` is negative (ink rises above the baseline), ``bottom`` positive.
    drawtext anchors a text's ink-top to its ``y``, so a lone word with no
    ascender ("warm,") has a smaller |top| than the full line and, drawn at
    the same ``y``, its baseline lands higher — that's the up/down jitter.
    The caller offsets each highlight word's ``y`` by (word_top - line_top)
    so every word's baseline lands on the line's baseline instead.
    """
    if not text or font is None:
        return (0.0, 0.0)
    try:  # Pillow ≥ 8: baseline-relative box.
        b = font.getbbox(text, anchor="ls")
        return (float(b[1]), float(b[3]))
    except Exception:  # noqa: BLE001
        pass
    try:  # Older Pillow: ascender-relative box → shift by the ascent.
        asc = float(font.getmetrics()[0])
        b = font.getbbox(text)
        return (float(b[1]) - asc, float(b[3]) - asc)
    except Exception:  # noqa: BLE001
        pass
    try:  # Last resort: font-wide metrics (no per-word distinction).
        asc, desc = font.getmetrics()
        return (-float(asc), float(desc))
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def _text_advance(text: str, font) -> float:
    """Pen-advance width of ``text`` for a loaded PIL font.

    ``getlength`` (Pillow ≥ 8) is the horizontal pen movement — exactly what
    decides where the next glyph starts — so it's the right measure for lining
    a per-word highlight drawtext up with its place in the base line. Falls
    back to the ink-bbox helper, then to a glyph-advance estimate.
    """
    if not text:
        return 0.0
    if font is not None:
        try:
            return float(font.getlength(text))
        except Exception:  # noqa: BLE001 — any failure → coarser measure
            return _measure_text_width(text, font)
    return _measure_text_width(text, font)


def _resolve_caption_style(cap):
    """Resolve the shared per-caption drawtext style from an overlay dict.

    Returns a dict of the pieces every drawtext for this caption reuses
    (font / colors / stroke / box / x / y expressions).
    """
    font_path = _resolve_caption_font_file(cap.get("fontFamily") or "", 700)
    # Client feedback: captions read too small. Scale every caption up by
    # CAPTION_FONT_SCALE (default 1.2 = +20%); set it to 1.0 to restore the
    # authored size. Applies to existing casts too (render-time only).
    _font_scale = _env_float_clamped("CAPTION_FONT_SCALE", 1.2, 0.5, 3.0)
    font_size = max(1, int(round(int(cap.get("fontSize") or 44) * _font_scale)))
    font_color = _drawtext_color(cap.get("fontColor"), default="white")
    stroke_width = int(cap.get("strokeWidth") or 0)
    stroke_color = _drawtext_color(cap.get("strokeColor"), default="black")
    # Karaoke highlight color; default to the base color (→ no highlight layer).
    highlight_color = _drawtext_color(cap.get("highlightColor"), default=font_color)

    # Horizontal: honour textAlign; default matches the old hardcoded ASS
    # Alignment=2 (bottom-center).
    align = (cap.get("textAlign") or "center").lower()
    if align == "left":
        x_expr = "40"
    elif align == "right":
        x_expr = "w-text_w-40"
    else:
        x_expr = "(w-text_w)/2"

    # Vertical: positionY is a 0..1 fraction of canvas height from the top,
    # set by the caption preset (editorStarterMapping.ts presetPositionFraction).
    # Falls back to a bottom margin for legacy items.
    #
    # Client feedback: bottom captions sit low enough that the video player's
    # control bar covers them. Lift a provided fraction by CAPTION_Y_LIFT_FRAC
    # (default 0.06 of canvas height); for the no-preset fallback, sit
    # CAPTION_BOTTOM_MARGIN_FRAC up from the bottom (default 0.14 ≈ clears a
    # standard control bar) instead of a fixed 40 px. Set both to their old
    # values (0.0 / ~0.021) to restore prior placement.
    _y_lift = _env_float_clamped("CAPTION_Y_LIFT_FRAC", 0.06, 0.0, 0.5)
    _bottom_margin_frac = _env_float_clamped("CAPTION_BOTTOM_MARGIN_FRAC", 0.14, 0.0, 0.5)
    position_y = cap.get("positionY")
    if isinstance(position_y, (int, float)):
        _py = float(position_y)
        # Only lift lower-third / bottom captions (>= 0.6) — those are the
        # ones the player's control bar covers. Centre / top are left alone.
        if _py >= 0.6:
            _py = max(0.0, _py - _y_lift)
        position_y_frac = max(0.0, min(1.0, _py))
        y_expr = f"(h-text_h)*{position_y_frac:.4f}"
    else:
        position_y_frac = None
        y_expr = f"h-text_h-(h*{_bottom_margin_frac:.4f})"

    box_args = ""
    if cap.get("ffmpegBoxEnabled"):
        box_color = _drawtext_color(cap.get("ffmpegBoxColor"), default="black@0.5")
        box_args = f"box=1:boxcolor={box_color}:boxborderw=12:"

    return {
        "font_path": font_path,
        "font_size": font_size,
        "font_color": font_color,
        "stroke_width": stroke_width,
        "stroke_color": stroke_color,
        "highlight_color": highlight_color,
        "x_expr": x_expr,
        "y_expr": y_expr,
        # Raw 0..1 fraction (None = bottom-anchor default) so the per-word
        # highlight path can resolve ONE constant pixel y for the whole line.
        "position_y_frac": position_y_frac,
        "box_args": box_args,
    }


def _caption_drawtext(in_label, out_label, textfile_path, style, start, end,
                      *, color, x_override=None, y_override=None, with_box=True):
    """One drawtext filter for a caption page (or a highlighted word).

    The text lives in ``textfile_path`` and is referenced with ``textfile=`` +
    ``expansion=none`` — it is NEVER inlined as ``text='...'``. Inlining forced
    every apostrophe / colon / comma / newline in the caption to survive
    FFmpeg's filtergraph quote parser, and a wrapped caption containing an
    apostrophe desynced it: the ``text=`` value swallowed the filter's own
    trailing options and drawtext burned ``:fontsize=42:...:enable=between(...)``
    into the frame as literal text. A sidecar file sidesteps all filtergraph
    escaping; ``expansion=none`` also stops drawtext interpreting ``%{...}`` /
    backslash sequences in the caption body. Real newlines in the file stay
    hard line breaks.
    """
    box = style["box_args"] if with_box else ""
    x = x_override or style["x_expr"]
    y = y_override or style["y_expr"]
    return (
        f"{in_label}drawtext="
        f"fontfile='{style['font_path']}':"
        f"textfile='{textfile_path}':expansion=none:"
        f"fontsize={style['font_size']}:"
        f"fontcolor={color}:"
        f"{box}"
        f"line_spacing=6:fix_bounds=1:"
        f"borderw={style['stroke_width']}:bordercolor={style['stroke_color']}:"
        f"x={x}:y={y}:"
        f"enable='between(t,{start:.3f},{end:.3f})'"
        f"{out_label}"
    )


def _build_caption_filter_parts(caption_overlays, current_label, *, canvas_w, canvas_h, tmpdir):
    """Build the drawtext filtergraph parts that burn styled captions in.

    When the caption overlay carries per-word timing (``_captions_tokens``,
    forwarded by tasks/cast_render.extract_overlay_elements from
    routers/casts/timeline.py), a long caption is paged ~6-7 words at a time —
    each page enabled only for its own spoken window — and, if a distinct
    ``highlightColor`` is set, the currently-spoken word is restated on top in
    that color. This mirrors the editor preview (captions-layer.tsx) and
    cast_ffmpeg_composer's drawtext path. Without word timing it falls back to
    one static drawtext for the whole caption window.

    Every caption gets its own drawtext carrying its own resolved style (from
    the editor's caption preset) rather than one hardcoded style for all.

    Returns ``(filter_parts, current_label, drawn_count)`` where drawn_count is
    the number of caption *elements* that produced at least one filter. Pulled
    out of ``_run_ffmpeg_compose`` so the chain can be unit-tested without
    invoking FFmpeg, matching ``_build_overlay_filter_parts``.
    """
    filter_parts: list[str] = []
    if not caption_overlays or SKIP_CAPTION_BURN_IN:
        return filter_parts, current_label, 0

    sorted_caps = sorted(caption_overlays, key=lambda c: c.get("start_s", 0))
    safe_width = _caption_safe_width(canvas_w)
    seq = 0  # unique drawtext label counter across every page / highlight
    drawn = 0

    for cap in sorted_caps:
        raw_text = (cap.get("text") or "").strip()
        try:
            cap_start = float(cap.get("start_s", 0))
            cap_end = float(cap.get("end_s", cap_start + 1))
        except (TypeError, ValueError) as ex:
            sentry_sdk.capture_exception(ex)
            continue
        if cap_end <= cap_start:
            continue

        transform = (cap.get("textTransform") or "none").lower()

        def _xform(s: str) -> str:
            if transform == "uppercase":
                return s.upper()
            if transform == "lowercase":
                return s.lower()
            return s

        style = _resolve_caption_style(cap)
        measure_font = _caption_font(style["font_path"], style["font_size"])
        req_w = cap.get("captionWidth") or cap.get("width")
        caption_width = min(int(req_w), safe_width) if req_w else safe_width
        max_lines = int(cap.get("maxLines") or 1)
        page_ms = max(500, int(cap.get("pageDurationInMilliseconds") or 3500))

        # Time-windowed pages from per-word timing, then re-split so each page
        # fits BOTH the one-line char budget and the _MAX_WORDS_PER_PAGE (~7)
        # ceiling — identical to cast_ffmpeg_composer / the editor preview.
        tokens = cap.get("_captions_tokens") or []
        pages = _group_caption_tokens_into_pages(tokens, page_ms)
        budget = _chars_per_line(style["font_size"], caption_width) * max(1, max_lines)
        capped: list[dict] = []
        for pg in pages:
            if (
                len(pg["text"]) <= budget
                and len(pg.get("tokens") or []) <= _MAX_WORDS_PER_PAGE
            ):
                capped.append(pg)
            else:
                capped.extend(
                    cap_tokens_to_line_budget(
                        pg["tokens"],
                        font_size=style["font_size"],
                        caption_width=caption_width,
                        max_lines=max_lines,
                    )
                )
        pages = capped
        produced = False

        if not pages:
            # No word timing — one static line for the whole caption window.
            if not raw_text:
                continue
            wrapped = _wrap_text_to_width(
                _xform(raw_text), measure_font, float(caption_width)
            )
            if not wrapped:
                continue
            path = os.path.join(tmpdir, f"caption_{seq}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(wrapped))
            out_label = f"[cap{seq}]"
            filter_parts.append(
                _caption_drawtext(
                    current_label, out_label, path, style, cap_start, cap_end,
                    color=style["font_color"],
                )
            )
            current_label = out_label
            seq += 1
            produced = True
        else:
            for idx, pg in enumerate(pages):
                pg_text = _xform(pg["text"])
                if not pg_text.strip():
                    continue
                wrapped = _wrap_text_to_width(
                    pg_text, measure_font, float(caption_width)
                )
                if not wrapped:
                    continue
                pg_start = max(pg["start_ms"] / 1000.0, cap_start)
                # Keep each page on screen until the NEXT page appears (last
                # page until the block ends), so the caption doesn't blink off
                # during inter-word pauses — matches the editor preview.
                if idx + 1 < len(pages):
                    pg_end = min(pages[idx + 1]["start_ms"] / 1000.0, cap_end)
                else:
                    pg_end = cap_end
                if pg_end <= pg_start:
                    continue

                page_tokens = pg.get("tokens") or []
                want_highlight = bool(
                    style["highlight_color"]
                    and style["highlight_color"] != style["font_color"]
                    and len(wrapped) == 1
                    and not style["box_args"]
                    and page_tokens
                )

                if not want_highlight:
                    # One centered drawtext for the whole page.
                    path = os.path.join(tmpdir, f"caption_{seq}.txt")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(wrapped))
                    out_label = f"[cap{seq}]"
                    filter_parts.append(
                        _caption_drawtext(
                            current_label, out_label, path, style,
                            pg_start, pg_end, color=style["font_color"],
                        )
                    )
                    current_label = out_label
                    seq += 1
                    produced = True
                    continue

                # Karaoke highlight. drawtext can't recolor a sub-string, so:
                #  1. the whole line is drawn ONCE, base colour — FFmpeg's own
                #     layout keeps the words on one baseline and correctly
                #     spaced;
                #  2. each spoken word is redrawn on top in the highlight
                #     colour for its [start, end] window, at the SAME x it
                #     occupies in the base line (measured pen-advance) and a
                #     y nudged so ITS baseline lands on the LINE's baseline.
                #
                # The y nudge matters: drawtext anchors a text's ink-top to y,
                # and a lone word with no ascender ("warm,") has a shorter ink
                # box than the full line, so at a shared y its baseline floats
                # up. Offsetting by (word_ink_top - line_ink_top) drops it back
                # onto the line. Word x/y are numeric off the SAME measured
                # line box as the base draw, so the two never drift apart —
                # any PIL-vs-FFmpeg metric error just shifts the whole caption
                # a pixel, base and highlight together.
                line = wrapped[0]
                line_w = _text_advance(line, measure_font)
                line_top, line_bot = _ink_bounds(line, measure_font)
                line_h = line_bot - line_top
                if line_h <= 1:
                    line_h = _font_line_height(measure_font, style["font_size"])
                if style["position_y_frac"] is not None:
                    base_line_y = round((canvas_h - line_h) * style["position_y_frac"])
                else:
                    base_line_y = round(canvas_h - line_h - 40)
                base_line_y = max(0, base_line_y)
                base_x = f"(w-{line_w:.1f})/2"

                base_path = os.path.join(tmpdir, f"caption_{seq}.txt")
                with open(base_path, "w", encoding="utf-8") as f:
                    f.write(line)
                base_label = f"[cap{seq}]"
                filter_parts.append(
                    _caption_drawtext(
                        current_label, base_label, base_path, style,
                        pg_start, pg_end, color=style["font_color"],
                        x_override=base_x, y_override=str(base_line_y),
                    )
                )
                current_label = base_label
                seq += 1
                produced = True

                words = [_xform((t.get("text") or "").strip()) for t in page_tokens]
                for wi, (tok, word) in enumerate(zip(page_tokens, words)):
                    if not word:
                        continue
                    t0 = max(int(tok.get("startMs", 0)) / 1000.0, pg_start)
                    t1 = min(int(tok.get("endMs", 0)) / 1000.0, pg_end)
                    if t1 <= t0:
                        continue
                    # x: left edge of this word within the line = advance of
                    # the line up to and including it, minus the word itself.
                    prefix_with_word = " ".join(w for w in words[: wi + 1] if w)
                    offset = _text_advance(prefix_with_word, measure_font) \
                        - _text_advance(word, measure_font)
                    word_x = f"(w-{line_w:.1f})/2+{max(0.0, offset):.1f}"
                    # y: drop a short word so its baseline meets the line's.
                    word_top, _ = _ink_bounds(word, measure_font)
                    word_y = base_line_y + max(0, round(word_top - line_top))

                    hl_path = os.path.join(tmpdir, f"caption_{seq}.txt")
                    with open(hl_path, "w", encoding="utf-8") as f:
                        f.write(word)
                    hl_label = f"[cap{seq}]"
                    filter_parts.append(
                        _caption_drawtext(
                            current_label, hl_label, hl_path, style, t0, t1,
                            color=style["highlight_color"],
                            x_override=word_x, y_override=str(word_y),
                            with_box=False,
                        )
                    )
                    current_label = hl_label
                    seq += 1

        if produced:
            drawn += 1

    return filter_parts, current_label, drawn


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
    # A b-roll tagged _pip_bg is a PIP/talking-head block's background — it is
    # composited BEHIND the face during clip normalization below, so it must
    # not also be laid over the whole frame in the top overlay pass.
    overlay_elements = [ov for ov in overlay_elements if not ov.get("_pip_bg")]

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

            norm = os.path.join(tmpdir, f"norm_v_{block_id or len(normalized_videos)}.mp4")
            # Clips arrive already trimmed to exactly their slot by the
            # orchestrator's overshoot+trim pipeline
            # (services.block_extension.trim_block_to_slot). The worker only
            # conforms canvas dims/fps and trims any residual surplus from
            # the head — it NEVER pads / ping-pongs / reverses to fill a
            # short slot (those produced the "walks backwards" / "freeze"
            # artefacts). If a clip is still short, the slot shows canvas
            # background for the remainder rather than a synthesized fill.
            src_dur = _probe_duration_s(raw)
            if src_dur <= 0:
                src_dur = slot_dur
            overshoot_s = max(0.0, src_dur - slot_dur)
            v_prep = (
                f"[0:v]fps={canvas_fps},"
                f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"trim=start={overshoot_s:.3f}:duration={slot_dur:.3f},"
                f"setpts=PTS-STARTPTS[vout]"
            )
            cmd = [
                "ffmpeg", "-y", "-loglevel", "warning", "-i", raw,
                "-filter_complex", v_prep,
                "-map", "[vout]",
                "-r", str(canvas_fps),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-pix_fmt", "yuv420p",
                "-an",
                norm,
            ]
            if overshoot_s > 0.05:
                logger.info(
                    "compose %s: block=%s head-trim %.2fs surplus "
                    "(src=%.2fs slot=%.2fs)",
                    render_id, block_id, overshoot_s, src_dur, slot_dur,
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

            # ── PIP / talking-head: shrink this clip into a corner window on
            #    top of its background (the block's full-frame b-roll when it
            #    has one, else black) so the face is visible instead of the
            #    b-roll covering it. Non-fatal — on failure keep the
            #    full-frame clip.
            pip = vt.get("pip") if isinstance(vt.get("pip"), dict) else None
            if pip and int(pip.get("w") or 0) > 0 and int(pip.get("h") or 0) > 0:
                pw, ph = int(pip["w"]), int(pip["h"])
                px, py = int(pip.get("x") or 0), int(pip.get("y") or 0)
                bg_local = None
                bg_src = pip.get("bg_src")
                if bg_src:
                    bg_local = os.path.join(
                        tmpdir, f"pipbg_{block_id or len(normalized_videos)}.mp4"
                    )
                    try:
                        _download(bg_src, bg_local, timeout=block_timeout(slot_dur))
                    except Exception as ex:
                        sentry_sdk.capture_exception(ex)
                        logger.warning(
                            "compose %s: PIP bg download failed block=%s (%s); "
                            "using black background",
                            render_id, block_id, ex,
                        )
                        bg_local = None

                pip_out = os.path.join(
                    tmpdir, f"pip_v_{block_id or len(normalized_videos)}.mp4"
                )
                # Input 0 is a slot-length black canvas — it anchors the output
                # duration so a short b-roll can't truncate the slot.
                pip_inputs = [
                    "-f", "lavfi", "-t", f"{slot_dur:.3f}",
                    "-i", f"color=c=black:s={canvas_w}x{canvas_h}:r={canvas_fps}",
                    "-i", norm,
                ]
                if bg_local:
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
                else:
                    fc = (
                        f"[1:v]scale={pw}:{ph}:force_original_aspect_ratio=increase,"
                        f"crop={pw}:{ph},setsar=1[fg];"
                        f"[0:v][fg]overlay={px}:{py}:shortest=1[vout]"
                    )
                pip_cmd = [
                    "ffmpeg", "-y", "-loglevel", "warning",
                    *pip_inputs,
                    "-filter_complex", fc,
                    "-map", "[vout]",
                    "-r", str(canvas_fps),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-pix_fmt", "yuv420p", "-an",
                    pip_out,
                ]
                try:
                    _run(
                        pip_cmd, timeout=block_timeout(slot_dur),
                        label=f"pip_composite_{block_id}",
                    )
                    norm = pip_out
                    logger.info(
                        "compose %s: PIP composite block=%s %dx%d @ (%d,%d) bg=%s",
                        render_id, block_id, pw, ph, px, py, bool(bg_local),
                    )
                except Exception as ex:
                    sentry_sdk.capture_exception(ex)
                    logger.warning(
                        "compose %s: PIP composite failed block=%s (%s); "
                        "keeping full-frame clip",
                        render_id, block_id, ex,
                    )

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

            # Non-mic-on blocks get the lo-fi "recorded on a phone" VO texture
            # prepended to the normalize chain; mic-on blocks stay clean.
            # async=1000 lets aresample correct timestamp gaps (up to 1000
            # samples/s) so a per-clip A/V mismatch does not leak into the
            # slot-aligned mix as lipsync drift.
            af = "aresample=48000:async=1000,aformat=channel_layouts=stereo,apad"
            if phone_mic_filter_enabled() and not block_is_mic_on(at):
                af = f"{phone_mic_filter_chain()},{af}"

            norm = os.path.join(tmpdir, f"norm_a_{block_id or len(normalized_audios)}.m4a")
            cmd = [
                "ffmpeg", "-y", "-loglevel", "warning", "-i", raw,
                "-af", af,
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
            # PR-D: bridge the gap to the next clip with subtle motion instead
            # of a frozen last frame. When the gap is within the bounded range
            # we take the clip's trailing `_TAIL_REVERSE_SEGMENT_S`, build a
            # forward→reverse bounce, loop it to cover the gap, and concat it
            # after the clip — natural motion, no freeze. A held last frame
            # (tpad=stop_mode=clone) remains the fallback for sub-frame gaps or
            # gaps too large for a tasteful bounce. setpts then shifts the whole
            # extended stream to land at canvas time s.
            use_reverse_bounce = (
                TAIL_REVERSE_LOOP_ENABLED
                and 1e-3 < hold_s <= _TAIL_REVERSE_LOOP_MAX_GAP_S
            )
            if use_reverse_bounce:
                fps_f = float(canvas_fps if canvas_fps > 0 else 30)
                clip_dur = float(v.get("dur") or 0.0)
                seg = min(_TAIL_REVERSE_SEGMENT_S, clip_dur) if clip_dur > 0 else _TAIL_REVERSE_SEGMENT_S
                seg = max(seg, 1.0 / fps_f)
                seg_start = max(0.0, clip_dur - seg) if clip_dur > 0 else 0.0
                # ``loop`` buffers ``size`` frames; ``size`` must be the bounce's
                # own (small) frame count, never a huge constant — a large size
                # makes ffmpeg buffer thousands of frames and hang. ``loop`` is
                # the EXTRA repeat count after the first pass.
                bounce_frames = max(1, int(round(2.0 * seg * fps_f)))
                loops_needed = max(0, int(hold_s / max(2.0 * seg, 1.0 / fps_f)) + 1)
                filter_parts.append(
                    f"[{in_idx}:v]fps={canvas_fps},setpts=PTS-STARTPTS,"
                    f"split=2[{pre_label}body][{pre_label}tsrc];"
                    f"[{pre_label}tsrc]trim=start={seg_start:.3f},setpts=PTS-STARTPTS,"
                    f"split=2[{pre_label}fwd][{pre_label}rsrc];"
                    f"[{pre_label}rsrc]reverse[{pre_label}rev];"
                    f"[{pre_label}fwd][{pre_label}rev]concat=n=2:v=1:a=0,"
                    f"setpts=PTS-STARTPTS[{pre_label}bnc];"
                    f"[{pre_label}bnc]loop=loop={loops_needed}:size={bounce_frames}:start=0,"
                    f"trim=duration={hold_s:.3f},setpts=PTS-STARTPTS[{pre_label}fill];"
                    f"[{pre_label}body][{pre_label}fill]concat=n=2:v=1:a=0,"
                    f"setpts=PTS-STARTPTS+{v['s']:.3f}/TB[{pre_label}]"
                )
            else:
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
                logger.info(
                    "[audio_mix_debug] track=%d block_id=%s s=%.3f e=%.3f "
                    "dur=%.3f delay_ms=%d path=%s",
                    j, a.get("block_id"), a.get("s", 0.0), a.get("e", 0.0),
                    a.get("dur", 0.0), delay_ms, a.get("path"),
                )
                label = f"a{j}d"
                filter_parts.append(
                    f"[{in_idx}:a]adelay={delay_ms}|{delay_ms},apad[{label}]"
                )
                mix_inputs.append(f"[{label}]")
            mix_in = "".join(mix_inputs)
            # normalize=0: these tracks are time-disjoint (each is delayed +
            # padded to its own [s,e] slot — confirmed contiguous, never
            # overlapping) rather than genuinely simultaneous sources like
            # music+dialogue. amix's default normalize=1 divides EVERY
            # input's volume by the total input count regardless of how many
            # are actually non-silent at a given instant, so a 10-block cast
            # had every voice flattened to ~1/10 volume uniformly. The
            # dynaudnorm pass after it was then aggressively re-normalizing
            # loudness on a rolling window to compensate — which, right at a
            # block boundary, can pull a quiet trailing artifact from the
            # ending block up into audibility just as the next block's
            # dialogue starts, sounding like two voices briefly overlapping.
            filter_parts.append(
                f"{mix_in}amix=inputs={len(mix_inputs)}:"
                f"duration=longest:dropout_transition=0:normalize=0,"
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

        # Image and video overlays
        overlay_parts, current_label = _build_overlay_filter_parts(
            overlay_elements, current_label
        )
        filter_parts.extend(overlay_parts)

        # Unify the look across avatar bake / AI b-roll / Pexels stock (each
        # graded differently at source) — one gentle pass on the whole
        # picture, BEFORE captions burn in so the text stays untinted. Off
        # unless COMPOSE_COLOR_GRADE_ENABLED; tune via COMPOSE_COLOR_GRADE_FILTER.
        _grade = color_grade_filter()
        if _grade:
            filter_parts.append(f"{current_label}{_grade}[graded]")
            current_label = "[graded]"
            logger.info("compose %s: applied colour grade — %s", render_id, _grade)

        # Captions used to burn in as one plain .srt file with a single
        # HARDCODED force_style (DejaVu Sans, white, fixed size/position)
        # applied to every line — the font/color/highlight/box/position
        # each caption actually carries (from the editor's caption preset)
        # was computed correctly all the way through extract_overlay_elements
        # but never read here, so every render showed the same generic
        # white caption regardless of what was styled in the editor. Each
        # caption now gets its own drawtext filter using its own resolved
        # style instead of one style for all of them.
        caption_parts, current_label, drawn = _build_caption_filter_parts(
            caption_overlays, current_label,
            canvas_w=canvas_w, canvas_h=canvas_h, tmpdir=tmpdir,
        )
        filter_parts.extend(caption_parts)
        logger.info(
            "compose %s: added %d styled drawtext caption(s)",
            render_id, drawn,
        )

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
