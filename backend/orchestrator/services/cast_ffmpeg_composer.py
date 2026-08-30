"""Cast FFmpeg Composer — translates a Twick timeline snapshot into an FFmpeg command.

Core rules:
- Bonded V1 snapshots are REPLACED with baked MP4 URLs (from InfiniteTalk Pass 1)
- A1 voice elements are SKIPPED — their audio is intrinsic to the baked MP4
- Non-bonded video/image elements (user-uploaded, stock) use their original src
- Music (A2/A3) is mixed with sidechain ducking against the narration
- Captions render via drawtext filters with word-level timing
- Product carousels (multiple media assets cycling through a block) collapse
  into a single xfade-chained video stream per block, then overlay normally
- Output encodes with h264_nvenc on GPU worker
"""
import glob
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

import sentry_sdk

logger = logging.getLogger(__name__)


def _captions_ssr_enabled() -> bool:
    """Feature flag gating the SSR caption overlay path (Step 12).

    Default ON; set ``CAPTIONS_SSR_ENABLED=false`` to roll back to the legacy
    drawtext burn path. Any failure inside the SSR path also falls back to
    drawtext automatically, so this only controls which path is *attempted*
    first.
    """
    return (os.getenv("CAPTIONS_SSR_ENABLED") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# ── Caption style defaults (TikTok-style burnt captions) ──────────────────
# Best-in-class short-form captions (Submagic / Captions.ai style): a bold
# geometric sans, smaller per-word text, a punchy highlight color for the
# active word, white base text, and a black stroke so the words pop over any
# background. Each knob is overridable via env var so a render can be tuned
# without a code change. Values here are calibrated for the 480x848 test
# canvas; sizes are still scaled per-element on the orchestrator side.
_CAPTION_DEFAULT_FONT_SIZE = 22
_CAPTION_DEFAULT_FONT_FAMILY = "Montserrat Bold"
_CAPTION_DEFAULT_HIGHLIGHT_COLOR = "#FFEB3B"  # bright yellow active word
_CAPTION_DEFAULT_BASE_COLOR = "#FFFFFF"
_CAPTION_DEFAULT_STROKE_COLOR = "#000000"
_CAPTION_DEFAULT_STROKE_WIDTH = 2  # px, scaled for 480x848 (≈3px on 720p)
# Vertical anchor as a fraction of canvas height. 0.70 ≈ 30% up from the
# bottom (rule of thirds) — captions stay clearly visible without crowding
# the very bottom safe zone.
_CAPTION_DEFAULT_POSITION_Y = 0.70


def _caption_default_font_size() -> int:
    raw = os.getenv("CAPTION_FONT_SIZE")
    if not raw:
        return _CAPTION_DEFAULT_FONT_SIZE
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return _CAPTION_DEFAULT_FONT_SIZE


def _caption_default_font_family() -> str:
    return (os.getenv("CAPTION_FONT_FAMILY") or _CAPTION_DEFAULT_FONT_FAMILY).strip() \
        or _CAPTION_DEFAULT_FONT_FAMILY


def _caption_default_highlight_color() -> str:
    return (os.getenv("CAPTION_HIGHLIGHT_COLOR") or _CAPTION_DEFAULT_HIGHLIGHT_COLOR).strip() \
        or _CAPTION_DEFAULT_HIGHLIGHT_COLOR


def _caption_default_base_color() -> str:
    return (os.getenv("CAPTION_BASE_COLOR") or _CAPTION_DEFAULT_BASE_COLOR).strip() \
        or _CAPTION_DEFAULT_BASE_COLOR


def _caption_default_stroke_color() -> str:
    return (os.getenv("CAPTION_STROKE_COLOR") or _CAPTION_DEFAULT_STROKE_COLOR).strip() \
        or _CAPTION_DEFAULT_STROKE_COLOR


def _caption_default_stroke_width() -> int:
    raw = os.getenv("CAPTION_STROKE_WIDTH")
    if not raw:
        return _CAPTION_DEFAULT_STROKE_WIDTH
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return _CAPTION_DEFAULT_STROKE_WIDTH


def _caption_default_position_y() -> float:
    raw = os.getenv("CAPTION_POSITION_Y")
    if not raw:
        return _CAPTION_DEFAULT_POSITION_Y
    try:
        return min(1.0, max(0.0, float(raw)))
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return _CAPTION_DEFAULT_POSITION_Y


# ── Carousel transition map (frontend label → FFmpeg xfade name) ──
# Frontend exposes 3 friendly labels; FFmpeg's xfade filter accepts dozens.
# We pick one canonical mapping each — slideleft moves left-to-right (which
# reads as "slide" to most users), zoomin grows the new image into frame.
XFADE_MAP: dict[str, str] = {
    "crossfade": "fade",
    "slide": "slideleft",
    "zoom": "zoomin",
}
# Half-second overlap between consecutive carousel items. Matches the
# mapper's `itemEnd = min(itemStart + speed + 0.5, end)` overhang so the
# fade window has source frames on both sides.
_CAROUSEL_XFADE_DURATION_S = 0.5


# ── Caption helpers (Fix 3 Phase A) ───────────────────────────────────────────────────

# We bundle Inter in /usr/share/fonts/truetype/inter on every container
# that runs the FFmpeg composer (orchestrator + GPU render worker). The
# preview also uses Inter, so picking the matching weight here keeps
# preview ≈ render for caption text.
_INTER_FONT_DIR = "/usr/share/fonts/truetype/inter"
_CUSTOM_FONT_DIR = "/usr/share/fonts/truetype/custom"
_INTER_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Caption preset fonts bundled by the orchestrator Dockerfile. Keys are
# lowercased fontFamily strings as they appear on the CaptionPreset; values
# are absolute paths to the TTF that FFmpeg's drawtext fontfile= will read.
# Anything not in this map falls through to the Inter weight resolver.
FONT_FILE_MAP: dict[str, str] = {
    "inter": f"{_INTER_FONT_DIR}/Inter-Bold.ttf",
    "montserrat": f"{_CUSTOM_FONT_DIR}/Montserrat.ttf",
    "anton": f"{_CUSTOM_FONT_DIR}/Anton.ttf",
    "bebas neue": f"{_CUSTOM_FONT_DIR}/BebasNeue.ttf",
    "caveat": f"{_CUSTOM_FONT_DIR}/Caveat.ttf",
    "playfair display": f"{_CUSTOM_FONT_DIR}/PlayfairDisplay.ttf",
    "bangers": f"{_CUSTOM_FONT_DIR}/Bangers.ttf",
    "comic neue": f"{_CUSTOM_FONT_DIR}/ComicNeue-Bold.ttf",
    "indie flower": f"{_CUSTOM_FONT_DIR}/IndieFlower.ttf",
    "courier new": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
}


def _resolve_caption_font_file(font_family: str, font_weight: int) -> str:
    """Return the absolute path to a TTF for (font_family, font_weight).

    Order of resolution:
    1. Direct hit on FONT_FILE_MAP (preset-bundled families like
       Montserrat, Bebas Neue, Caveat, etc.).
    2. Inter weight ladder for "inter" / generic sans families.
    3. DejaVu Bold fallback so FFmpeg always gets a real file path.
    """
    fam = (font_family or "").strip().lower()

    # Strip a trailing weight/style word ("Montserrat Bold" → "montserrat")
    # so families that carry an inline weight still hit FONT_FILE_MAP. The
    # bundled Montserrat is a variable font that already contains Bold, so the
    # path resolves the same either way.
    _WEIGHT_WORDS = {
        "thin", "extralight", "light", "regular", "medium", "semibold",
        "demibold", "bold", "extrabold", "black", "heavy",
    }
    parts = fam.split()
    if len(parts) > 1 and parts[-1] in _WEIGHT_WORDS:
        base_fam = " ".join(parts[:-1])
    else:
        base_fam = fam

    # Preset-bundled family hit.
    for key in (fam, base_fam):
        if key in FONT_FILE_MAP:
            candidate = FONT_FILE_MAP[key]
            if os.path.isfile(candidate):
                return candidate

    # Inter weight ladder.
    if fam.startswith("inter") or fam in ("", "sans-serif", "system-ui", "tiktok sans"):
        weight_map = {
            300: "Inter-Light.ttf",
            400: "Inter-Regular.ttf",
            500: "Inter-Medium.ttf",
            600: "Inter-SemiBold.ttf",
            700: "Inter-Bold.ttf",
            800: "Inter-ExtraBold.ttf",
            900: "Inter-Black.ttf",
        }
        weights = sorted(weight_map.keys(), key=lambda w: abs(w - font_weight))
        for w in weights:
            candidate = os.path.join(_INTER_FONT_DIR, weight_map[w])
            if os.path.isfile(candidate):
                return candidate

    if os.path.isfile(_INTER_FALLBACK):
        return _INTER_FALLBACK
    return _INTER_FALLBACK


def _ffmpeg_escape_drawtext(text: str) -> str:
    """Escape special characters that break FFmpeg's drawtext filter.

    Per https://ffmpeg.org/ffmpeg-filters.html#drawtext we need to
    escape backslashes, single quotes, colons, commas, and percent.
    """
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "'\\''")
    s = s.replace(":", "\\:")
    s = s.replace(",", "\\,")
    s = s.replace("%", "\\%")
    return s


def _group_caption_tokens_into_pages(
    tokens: list[Any], max_page_ms: int
) -> list[dict]:
    """Group word-level Caption tokens into TikTok-style pages.

    Mirrors the preview's @remotion/captions createTikTokStyleCaptions:
    accumulate tokens until the running window would exceed
    max_page_ms, then emit the current page and start a new one. Each
    output page = {text, start_ms, end_ms, tokens}.

    Tokens are expected to look like {text, startMs, endMs}; legacy
    {text, startInSeconds, endInSeconds} shapes are also accepted so
    older saved timelines still render.
    """
    norm: list[dict] = []
    for t in tokens or []:
        if not isinstance(t, dict):
            continue
        text = (t.get("text") or "").strip()
        if not text:
            continue
        if "startMs" in t and t.get("startMs") is not None:
            start_ms = int(t.get("startMs") or 0)
            end_ms = int(t.get("endMs") or start_ms)
        else:
            start_ms = int(round(float(t.get("startInSeconds") or 0) * 1000))
            end_ms = int(round(float(t.get("endInSeconds") or 0) * 1000))
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        norm.append({"text": text, "startMs": start_ms, "endMs": end_ms})

    if not norm:
        return []

    pages: list[dict] = []
    current: list[dict] = []
    page_start: int | None = None
    for tok in norm:
        if not current:
            current = [tok]
            page_start = tok["startMs"]
            continue
        # If adding this token would push the page over max_page_ms, flush.
        page_running_ms = tok["endMs"] - (page_start if page_start is not None else tok["startMs"])
        if page_running_ms > max_page_ms:
            pages.append({
                "text": " ".join(t["text"] for t in current),
                "start_ms": current[0]["startMs"],
                "end_ms": current[-1]["endMs"],
                "tokens": current,
            })
            current = [tok]
            page_start = tok["startMs"]
        else:
            current.append(tok)
    if current:
        pages.append({
            "text": " ".join(t["text"] for t in current),
            "start_ms": current[0]["startMs"],
            "end_ms": current[-1]["endMs"],
            "tokens": current,
        })
    return pages


# ── Caption wrap budget (regr-2c) ──────────────────────────────────────────────
#
# A time-windowed page (createTikTokStyleCaptions) can accumulate more text
# than fits in MAX_CAPTION_LINES at the configured font size / canvas width.
# When that happens the extra lines wrap past the safe area and the tail words
# get cropped off-frame (regr-2c: "...adjusts in real time" lost its tail). To
# guarantee captions never crop their last words we cap each visible chunk to a
# character budget derived from how many glyphs fit on one line, splitting any
# overflow into the next chunk while preserving each token's own timestamps.

# Default proportion of the canvas height reserved as a bottom safe margin so
# the centred caption block never runs off the bottom edge. Env-overridable;
# forwarded to the renderer which positions the text within the remaining area.
DEFAULT_SAFE_AREA_BOTTOM_PCT = 18.0

# Empirical average glyph advance as a fraction of the font size for the bold
# sans fonts the presets use (Montserrat/Inter ~0.52em incl. spaces). Used only
# to ESTIMATE how many characters fit on one line so we can budget a chunk; the
# renderer still measures real metrics and auto-shrinks as a fail-safe.
_AVG_GLYPH_EM = 0.52


def _env_float_clamped(name: str, default: float, lo: float, hi: float) -> float:
    """Read an env var as a float clamped to [lo, hi], falling back to default."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, val))


def _env_int_clamped(name: str, default: int, lo: int, hi: int) -> int:
    """Read an env var as an int clamped to [lo, hi], falling back to default."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, val))


def _safe_area_bottom_pct() -> float:
    """Bottom safe-area margin as a percentage of canvas height (env-tunable)."""
    # Clamp to a sane band so a bad env value can't hide the captions entirely.
    return _env_float_clamped(
        "CAPTIONS_SAFE_AREA_BOTTOM_PCT", DEFAULT_SAFE_AREA_BOTTOM_PCT, 0.0, 45.0
    )


# Sensible TikTok/Reels default: the bed sits ~-47 dB under the voice so it is
# felt, not heard. Matches the value users have been overriding to in env.
DEFAULT_MUSIC_VOLUME = 0.0044


def music_default_volume() -> float:
    """Background-music mix volume (env-tunable, clamped to a sane band).

    Defaults to ``DEFAULT_MUSIC_VOLUME`` so the bed sits far under the voice
    (TikTok/Reels loudness targets). Override with ``MUSIC_DEFAULT_VOLUME``.
    A per-cast or per-element override takes precedence over this; see
    ``resolve_music_volume``.
    """
    return _env_float_clamped("MUSIC_DEFAULT_VOLUME", DEFAULT_MUSIC_VOLUME, 0.0, 1.0)


def resolve_music_volume(
    elements: list[dict],
    cast_volume_override: float | None = None,
) -> tuple[float, str]:
    """Resolve the background-music volume and report where it came from.

    Precedence (highest first):
      1. ``element_prop`` — ``props.volume`` / ``metadata.volume`` stamped on a
         music element by the visual-studio volume slider.
      2. ``cast_column`` — the cast row's ``music_volume`` column.
      3. ``env_default`` — the ``MUSIC_DEFAULT_VOLUME`` env var.
      4. ``hardcoded`` — ``DEFAULT_MUSIC_VOLUME`` when the env is unset.

    Returns ``(volume, source)`` so callers can emit a single definitive
    ``[music-mix]`` log line and so tests can assert no path silently falls
    back to the hard-coded default when an override is present.
    """
    for el in elements or []:
        props = el.get("props") or {}
        meta = el.get("metadata") or {}
        raw = props.get("volume", meta.get("volume"))
        if raw is None:
            continue
        try:
            return max(0.0, min(1.0, float(raw))), "element_prop"
        except (TypeError, ValueError):
            continue

    if cast_volume_override is not None:
        try:
            return max(0.0, min(1.0, float(cast_volume_override))), "cast_column"
        except (TypeError, ValueError):
            pass

    # No element/cast override — fall to env, else the hard-coded default.
    source = "env_default" if os.getenv("MUSIC_DEFAULT_VOLUME") else "hardcoded"
    return music_default_volume(), source


def _resolve_music_volume(
    elements: list[dict],
    cast_volume_override: float | None = None,
) -> float:
    """Backwards-compatible shim returning only the resolved volume.

    Prefer ``resolve_music_volume`` which also reports the source. Retained so
    existing callers (e.g. the twick variant path) keep working unchanged.
    """
    vol, _ = resolve_music_volume(elements, cast_volume_override)
    return vol


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean env var. Truthy: 1/true/yes/on (case-insensitive)."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def phone_mic_filter_enabled() -> bool:
    """Whether to apply the phone-mic lo-fi filter to non-mic-on VO.

    Default ON. Override with ``PHONE_MIC_FILTER_NON_MICON=0``.
    """
    return _env_flag("PHONE_MIC_FILTER_NON_MICON", True)


def block_is_mic_on(block_or_meta) -> bool:
    """True when the given block / element-metadata represents a mic-on block.

    Accepts either a Block ORM object (``block.mic_on``) or a timeline
    element-metadata dict (``meta["mic_on"]``). A mic-on block keeps the clean
    studio voice; everything else is eligible for the phone-mic filter. Only an
    explicit truthy ``mic_on`` counts as mic-on — ``None``/unset is treated as
    non-mic-on so the lo-fi texture is the default.
    """
    if block_or_meta is None:
        return False
    if isinstance(block_or_meta, dict):
        return block_or_meta.get("mic_on") is True
    return getattr(block_or_meta, "mic_on", None) is True


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


# Absolute horizontal caption safe-area inset, in pixels per side. Bug A
# (Round 7): captions on product-overlay frames were clipped at the left edge
# because the usable width was computed relative to whatever block layer was on
# top. The safe area must be a CONSTANT absolute margin so caption x/width are
# identical on talking-head and product-overlay frames alike. Env-overridable.
DEFAULT_CAPTION_SAFE_INSET_PX = 32


def _caption_safe_inset(canvas_width: int) -> int:
    """Left/right safe-area inset in px so captions never touch the frame edge.

    Bug A: the user saw lines clip mid-word at the left/right edge on
    product-overlay frames. The inset is now an ABSOLUTE pixel margin
    (default 32px each side), NOT a percentage of the canvas or anything
    relative to the block/product layer — so the horizontal safe area is the
    same regardless of what is composited behind the caption. The
    ``canvas_width`` arg is kept for signature stability and to clamp the
    inset so it can never exceed half the canvas on a tiny frame.
    """
    inset = _env_int_clamped(
        "CAPTIONS_SAFE_AREA_INSET_PX", DEFAULT_CAPTION_SAFE_INSET_PX, 0, 10000
    )
    # Never let the two insets consume the whole frame on a small canvas.
    return max(0, min(inset, (canvas_width - 1) // 2))


def _caption_safe_width(canvas_width: int) -> int:
    """Maximum usable caption width inside the horizontal safe area.

    Pure absolute margin: ``canvas_width - 2 * safe_inset``. No relative
    ``canvas * 0.86`` term — that made the width (and the implied left margin
    when centred) depend on the canvas in a way that drifted between frame
    types. With a constant 32px inset on a 480px canvas this is 416px, leaving
    exactly 32px on each side on every frame.
    """
    inset = _caption_safe_inset(canvas_width)
    return max(1, int(canvas_width - 2 * inset))


def _caption_font(font_file: str, font_size: int):
    """Load a PIL font for measurement; fall back to the default bitmap font.

    Returns ``None`` when PIL itself is unavailable so callers degrade to the
    character-budget heuristic instead of crashing the render.
    """
    try:
        from PIL import ImageFont
    except Exception as e:  # pragma: no cover — PIL ships in the worker image
        sentry_sdk.capture_exception(e)
        return None
    try:
        return ImageFont.truetype(font_file, max(1, int(font_size)))
    except Exception as e:
        sentry_sdk.capture_exception(e)
        try:
            return ImageFont.load_default()
        except Exception as e2:
            sentry_sdk.capture_exception(e2)
            return None


def _measure_text_width(text: str, font) -> float:
    """Measure the pixel width of ``text`` with a loaded PIL font.

    Uses ``ImageFont.getbbox`` (Pillow ≥ 8). Falls back to the legacy
    ``getsize`` and finally to a glyph-advance estimate so measurement never
    raises into the caption path.
    """
    if not text:
        return 0.0
    if font is not None:
        try:
            bbox = font.getbbox(text)
            return float(bbox[2] - bbox[0])
        except Exception as e:
            sentry_sdk.capture_exception(e)
            try:
                return float(font.getsize(text)[0])
            except Exception as e2:
                sentry_sdk.capture_exception(e2)
    # No usable font — estimate from the average glyph advance.
    return float(len(text)) * 8.0


def _wrap_text_to_width(text: str, font, max_px: float) -> list[str]:
    """Break ``text`` into lines that each measure ≤ ``max_px`` at this font.

    Greedy word-boundary wrap measured with PIL so the result matches what
    FFmpeg's drawtext actually renders. A single word wider than ``max_px`` is
    kept on its own line rather than dropped — clipping one over-long token is
    preferable to losing it.
    """
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and _measure_text_width(candidate, font) > max_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _chars_per_line(font_size: int, caption_width: int) -> int:
    """Estimate how many characters fit on a single line at this font/width."""
    advance = max(1.0, float(font_size) * _AVG_GLYPH_EM)
    return max(1, int(caption_width / advance))


def cap_tokens_to_line_budget(
    tokens: list[dict],
    *,
    font_size: int,
    caption_width: int,
    max_lines: int,
) -> list[dict]:
    """Re-chunk word tokens so each visible chunk fits within ``max_lines``.

    Greedily accumulates tokens until adding the next word would exceed the
    character budget (``chars_per_line * max_lines``), then starts a new chunk.
    Each output chunk preserves the source token timestamps:

      * ``start_ms`` = first token's ``startMs``
      * ``end_ms``   = last token's ``endMs``

    so adjacent chunks never overlap (chunk N ends at its last token, chunk N+1
    starts at the next token's start). A single token longer than the budget is
    emitted as its own chunk rather than dropped — clipping one over-long word
    is preferable to losing it.

    Tokens are expected as ``{text, startMs, endMs}`` (already normalised).
    Returns page-like dicts ``{text, start_ms, end_ms, tokens}``.
    """
    budget = _chars_per_line(font_size, max(1, caption_width)) * max(1, max_lines)

    chunks: list[dict] = []
    current: list[dict] = []
    current_len = 0

    def _flush() -> None:
        if not current:
            return
        chunks.append(
            {
                "text": " ".join(t["text"] for t in current),
                "start_ms": current[0]["startMs"],
                "end_ms": current[-1]["endMs"],
                "tokens": list(current),
            }
        )

    for tok in tokens:
        word = tok["text"]
        # +1 for the joining space once the chunk already has a word.
        added = len(word) + (1 if current else 0)
        if current and current_len + added > budget:
            _flush()
            current = []
            current_len = 0
            added = len(word)
        current.append(tok)
        current_len += added

    _flush()
    return chunks


# ── SSR caption overlay helpers (Step 12) ─────────────────────────────────────

def _caption_tokens_to_ssr(tokens: list[Any]) -> list[dict]:
    """Convert a block's WhisperX word timings to the SSR token shape.

    The SSR service expects ``{text, startMs, endMs}`` with times RELATIVE to
    the block (first token at ~0), because it renders a standalone overlay of
    just this block's duration. We re-base every token on the earliest start so
    the overlay timeline begins at 0; the composer re-anchors it onto the
    absolute video timeline when it overlays.
    """
    norm: list[dict] = []
    for t in tokens or []:
        if not isinstance(t, dict):
            continue
        text = (t.get("text") or "").strip()
        if not text:
            continue
        if "startMs" in t and t.get("startMs") is not None:
            start_ms = int(t.get("startMs") or 0)
            end_ms = int(t.get("endMs") or start_ms)
        else:
            start_ms = int(round(float(t.get("startInSeconds") or 0) * 1000))
            end_ms = int(round(float(t.get("endInSeconds") or 0) * 1000))
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        norm.append({"text": text, "startMs": start_ms, "endMs": end_ms})

    if not norm:
        return []

    base = min(t["startMs"] for t in norm)
    return [
        {"text": t["text"], "startMs": t["startMs"] - base, "endMs": t["endMs"] - base}
        for t in norm
    ]


def _find_png_sequence_pattern(directory: str) -> str | None:
    """Return an FFmpeg image2 input pattern for a Remotion PNG sequence dir.

    Remotion's ``renderFrames`` writes zero-padded frames (default
    ``element-%04d.png``); we don't hardcode the prefix — we discover the
    actual prefix/pad width from the files on disk so a Remotion default change
    doesn't silently break the overlay. Returns ``None`` if the directory has
    no recognisable numbered PNG sequence.
    """
    try:
        pngs = sorted(glob.glob(os.path.join(directory, "*.png")))
    except OSError as e:
        sentry_sdk.capture_exception(e)
        return None
    if not pngs:
        return None
    # Match a trailing run of digits before .png (e.g. element-0000.png).
    rx = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)\.png$")
    first = os.path.basename(pngs[0])
    m = rx.match(first)
    if not m:
        return None
    prefix = m.group("prefix")
    pad = len(m.group("num"))
    return os.path.join(directory, f"{prefix}%0{pad}d.png")


@dataclass
class FFmpegInput:
    """A single -i input for FFmpeg.

    `pre_input_args` is for inputs that need flags between the previous
    `-i` and the next one — e.g. `-loop 1 -t 3.5` for a still image, or
    `-stream_loop -1 -t 3.5 -an` for a short video that should loop and
    drop its audio. build_ffmpeg_command emits them in order, immediately
    before this input's `-i`.
    """
    url: str
    label: str
    has_video: bool = True
    has_audio: bool = True
    pre_input_args: list[str] = field(default_factory=list)


@dataclass
class OverlayFilter:
    """An overlay image positioned on the canvas."""
    input_label: str
    x: int
    y: int
    width: int
    height: int
    enable_start: float
    enable_end: float


@dataclass
class DrawtextFilter:
    """A drawtext caption filter."""
    text: str
    x: str  # FFmpeg expression
    y: str  # FFmpeg expression
    font_size: int
    font_color: str
    enable_start: float
    enable_end: float
    font_file: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    border_w: int = 2
    border_color: str = "black"


@dataclass
class CompositionPlan:
    """Complete FFmpeg composition plan ready for execution."""
    inputs: list[FFmpegInput] = field(default_factory=list)
    filter_complex: str = ""
    output_map: list[str] = field(default_factory=list)
    output_key: str = ""
    canvas_width: int = 1080
    canvas_height: int = 1920
    # SSR caption overlay temp dirs to clean up after the render (Step 12).
    # Populated when the SSR path renders one or more overlays. The caller
    # should best-effort remove these once FFmpeg has consumed them.
    ssr_overlay_dirs: list[str] = field(default_factory=list)


def cleanup_ssr_overlays(plan: "CompositionPlan") -> None:
    """Best-effort removal of the SSR-emitted overlay temp dirs (Step 12).

    Call after FFmpeg has finished reading the overlays. Wrapped so a cleanup
    failure (e.g. permissions on the shared volume) never bubbles into the
    render result.
    """
    for d in plan.ssr_overlay_dirs:
        try:
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        except Exception as e:  # noqa: BLE001 — cleanup must not fail a render
            sentry_sdk.capture_exception(e)


def _try_ssr_caption_overlay(
    *,
    cap: dict,
    props: dict,
    cap_start_s: float,
    cap_end_s: float,
    canvas_width: int,
    canvas_height: int,
    all_inputs: list["FFmpegInput"],
    filters: list[str],
    current_v_label: str,
    ssr_overlay_dirs: list[str],
) -> str | None:
    """Render this caption block via SSR and append its overlay filter chain.

    Returns the new ``current_v_label`` on success, or ``None`` to signal the
    caller should fall back to the drawtext path for this block. On success the
    function has appended exactly one input to ``all_inputs`` and the overlay
    filter(s) to ``filters``; the PNG-sequence dir is recorded in
    ``ssr_overlay_dirs`` for cleanup.

    The SSR overlay is rendered on its own timeline starting at t=0 for the
    block's duration. We shift it onto the absolute timeline with ``setpts``
    (so frame 0 lands at ``cap_start_s``) and gate it with an ``enable`` window
    so it only paints during the block.
    """
    try:
        from services.captions_ssr_client import render_overlay  # local import

        tokens = _caption_tokens_to_ssr(props.get("_captions_tokens") or [])
        if not tokens:
            # No word timings → nothing for SSR to render; let the drawtext
            # path handle any legacy single-string fallback.
            return None

        fps = 30
        # preset id is cast-level; the caption element may carry it through as
        # presetId / captionPresetId. Default matches the SSR service default.
        preset_id = str(
            props.get("presetId")
            or props.get("captionPresetId")
            or props.get("caption_preset_id")
            or "hormozi_bold"
        )

        # Forward the per-block style fields so SSR mirrors the editor exactly
        # (same preset table + same overrides ⇒ no preview≠render drift).
        block: dict[str, Any] = {}
        if props.get("pageDurationInMilliseconds") is not None:
            block["pageDurationInMilliseconds"] = int(
                props.get("pageDurationInMilliseconds") or 0
            )
        if props.get("fontFamily"):
            block["fontFamily"] = str(props.get("fontFamily"))
        if props.get("lineHeight") is not None:
            block["lineHeight"] = float(props.get("lineHeight") or 0)
        if props.get("letterSpacing") is not None:
            block["letterSpacing"] = float(props.get("letterSpacing") or 0)
        if props.get("maxLines") is not None:
            block["maxLines"] = int(props.get("maxLines") or 0)
        if props.get("captionWidth") is not None:
            block["captionWidth"] = int(props.get("captionWidth") or 0)
        # regr-2c: reserve a bottom safe-area margin so the centred caption
        # block never runs off the bottom edge and crops its tail words. The
        # renderer positions the text within the remaining area and auto-shrinks
        # if a chunk still would not fit.
        block["safeBottomPct"] = _safe_area_bottom_pct()
        # regr-2c: cap each visible chunk to a character budget so it fits in
        # maxLines at the font size / caption width. The renderer splits any
        # over-budget page into the next time window (token timestamps kept).
        _cap_font = int(props.get("fontSize") or 48)
        _cap_width = int(props.get("captionWidth") or int(canvas_width * 0.9))
        # Single line by default (see max_lines note in the FFmpeg-drawtext path).
        _cap_lines = int(props.get("maxLines") or 1)
        block["maxCharsPerChunk"] = _chars_per_line(_cap_font, _cap_width) * max(
            1, _cap_lines
        )

        overlay = render_overlay(
            tokens=tokens,
            preset_id=preset_id,
            width=canvas_width,
            height=canvas_height,
            fps=fps,
            block=block or None,
        )
        if overlay is None:
            return None

        out_dir = str(overlay.output_path)

        if overlay.format == "alpha-video":
            # Single alpha .webm input.
            src = out_dir
            pre_input_args: list[str] = []
            ssr_overlay_dirs.append(os.path.dirname(out_dir) or out_dir)
        else:
            # PNG sequence directory → image2 input at the rendered fps.
            pattern = _find_png_sequence_pattern(out_dir)
            if not pattern:
                return None
            src = pattern
            pre_input_args = ["-framerate", str(overlay.fps or fps)]
            ssr_overlay_dirs.append(out_dir)

        inp_label = f"capssr_{len(all_inputs)}"
        ssr_idx = len(all_inputs)
        all_inputs.append(FFmpegInput(
            url=src,
            label=inp_label,
            has_video=True,
            has_audio=False,
            pre_input_args=pre_input_args,
        ))

        scaled_label = f"{inp_label}_scaled"
        shifted_label = f"{inp_label}_shifted"
        out_label = f"capssr_overlaid_{ssr_idx}"

        # Scale the overlay to the canvas (it should already match, but be
        # defensive against slot/canvas mismatches) and keep the alpha channel.
        filters.append(
            f"[{ssr_idx}:v]format=rgba,"
            f"scale={canvas_width}:{canvas_height}[{scaled_label}]"
        )
        # Shift the overlay's t=0 to the block's absolute start so its frames
        # line up with the spoken words.
        filters.append(
            f"[{scaled_label}]setpts=PTS+{cap_start_s:.3f}/TB[{shifted_label}]"
        )
        # Alpha-composite over the running canvas, gated to the block window.
        filters.append(
            f"[{current_v_label}][{shifted_label}]overlay=0:0:"
            f"enable='between(t,{cap_start_s:.3f},{cap_end_s:.3f})'"
            f"[{out_label}]"
        )
        return out_label
    except Exception as e:  # noqa: BLE001 — any failure → drawtext fallback
        sentry_sdk.capture_exception(e)
        logger.warning("SSR caption overlay failed for block; using drawtext: %s", e)
        return None


def translate_timeline_to_ffmpeg(
    timeline: dict,
    baked_urls: dict[str, str],
    render_id: str,
    canvas_width: int = 1080,
    canvas_height: int = 1920,
    cast_music_volume: float | None = None,
) -> CompositionPlan:
    """Translate a Twick timeline snapshot + baked URLs into an FFmpeg composition plan.

    Args:
        timeline: The Twick timeline JSON snapshot
        baked_urls: Mapping of V1 element IDs to baked MP4 public URLs
        render_id: Render ID for output key generation
        canvas_width: Output canvas width
        canvas_height: Output canvas height
        cast_music_volume: Optional per-cast music-volume override (the cast
            row's ``music_volume`` column). Used when no music element carries
            its own ``props.volume``; falls back to the env/hard-coded default.

    Returns:
        CompositionPlan ready for FFmpeg execution
    """
    tracks = timeline.get("tracks", [])
    inputs: list[FFmpegInput] = []
    overlays: list[OverlayFilter] = []
    captions: list[DrawtextFilter] = []
    music_inputs: list[FFmpegInput] = []

    # ── Collect elements by type ──
    bonded_segments: list[dict] = []  # V1 elements (sorted by start time)
    non_bonded_video: list[dict] = []
    music_elements: list[dict] = []
    sfx_elements: list[dict] = []
    caption_elements: list[dict] = []
    overlay_elements: list[dict] = []

    # Pre-pass: pull carousel-group elements aside so they don't get treated
    # as N independent overlays. They'll be composed via xfade into one
    # video stream per group, then overlaid as a single layer.
    all_elements: list[dict] = []
    for track in tracks:
        for el in track.get("elements", []):
            all_elements.append(el)
    _, carousel_groups = _group_carousel_elements(all_elements)
    carousel_element_ids: set[str] = set()
    for items in carousel_groups.values():
        for it in items:
            carousel_element_ids.add(it.get("id") or "")

    for track in tracks:
        track_type = track.get("type", "element")
        track_id = track.get("id", "")

        for el in track.get("elements", []):
            meta = el.get("metadata") or {}
            el_type = el.get("type", "")

            # Carousel group members — handled in the carousel pass below;
            # never emit them as independent overlays.
            if (el.get("id") in carousel_element_ids):
                continue

            # A1 voice — SKIP (audio is intrinsic to baked MP4)
            if meta.get("bonded") and meta.get("paired_video_element_id"):
                logger.debug("Skipping A1 voice element %s (consumed by InfiniteTalk)", el.get("id"))
                continue

            # Bonded V1 snapshot — replace with baked MP4
            if meta.get("bonded") and meta.get("block_id") and meta.get("paired_audio_element_id"):
                bonded_segments.append(el)
                continue

            # Non-bonded video/image on video tracks
            if track_type == "video" and el_type in ("video", "image"):
                if el_type == "image":
                    overlay_elements.append(el)
                else:
                    non_bonded_video.append(el)
                continue

            # SFX on audio tracks (non-bonded) — short full-volume accents,
            # mixed in WITHOUT sidechain ducking (unlike background music).
            if track_type == "audio" and not meta.get("bonded") and meta.get("kind") == "sfx":
                sfx_elements.append(el)
                continue

            # Music on audio tracks (non-bonded)
            if track_type == "audio" and not meta.get("bonded"):
                music_elements.append(el)
                continue

            # Captions
            if track_type == "caption" or el_type == "caption":
                caption_elements.append(el)
                continue

            # Product overlays / other elements
            if track_type == "element":
                overlay_elements.append(el)
                continue

    # Sort bonded segments by start time
    bonded_segments.sort(key=lambda e: e.get("s", 0))

    # ── Build FFmpeg inputs for bonded segments ──
    input_idx = 0
    segment_labels: list[tuple[str, str]] = []  # (video_label, audio_label) per segment

    for seg in bonded_segments:
        element_id = seg["id"]
        baked_url = baked_urls.get(element_id)
        if not baked_url:
            logger.warning("No baked URL for bonded element %s, using original src", element_id)
            baked_url = seg.get("props", {}).get("src", "")
        if not baked_url:
            continue

        label = f"seg{input_idx}"
        inputs.append(FFmpegInput(url=baked_url, label=label, has_video=True, has_audio=True))
        segment_labels.append((f"{label}_v", f"{label}_a"))
        input_idx += 1

    # ── Build FFmpeg inputs for music ──
    music_labels: list[str] = []
    for mus in music_elements:
        src = mus.get("props", {}).get("src", "")
        if not src:
            continue
        label = f"mus{len(music_labels)}"
        music_inputs.append(FFmpegInput(url=src, label=label, has_video=False, has_audio=True))
        music_labels.append(label)

    # ── Build FFmpeg inputs for SFX ──
    # Each SFX is delayed to its timeline start and mixed at element volume; no
    # ducking. Track (label, start_s, volume) so the mix step can place them.
    sfx_inputs: list[FFmpegInput] = []
    sfx_specs: list[tuple[str, float, float]] = []
    for sfx in sfx_elements:
        src = sfx.get("props", {}).get("src", "")
        if not src:
            continue
        meta = sfx.get("metadata") or {}
        try:
            start_s = float(sfx.get("s", 0) or 0)
        except (TypeError, ValueError):
            start_s = 0.0
        try:
            volume = float(meta.get("volume", 1.0) or 1.0)
        except (TypeError, ValueError):
            volume = 1.0
        label = f"sfx{len(sfx_specs)}"
        sfx_inputs.append(FFmpegInput(url=src, label=label, has_video=False, has_audio=True))
        sfx_specs.append((label, max(start_s, 0.0), volume))

    # ── Build FFmpeg inputs for non-bonded video overlays ──
    overlay_video_labels: list[tuple[str, dict]] = []
    for ov in non_bonded_video:
        src = ov.get("props", {}).get("src", "")
        if not src:
            continue
        label = f"ov{len(overlay_video_labels)}"
        # Stock overlay videos are purely visual; muting their audio prevents
        # it from duplicating over the avatar voice track during the mix.
        inputs.append(FFmpegInput(url=src, label=label, has_video=True, has_audio=False))
        overlay_video_labels.append((label, ov))

    # ── Build FFmpeg inputs for image overlays ──
    overlay_image_labels: list[tuple[str, dict]] = []
    for ov in overlay_elements:
        src = ov.get("props", {}).get("src", "")
        if not src:
            continue
        label = f"img{len(overlay_image_labels)}"
        inputs.append(FFmpegInput(url=src, label=label, has_video=True, has_audio=False))
        overlay_image_labels.append((label, ov))

    # All inputs list (segments + music + sfx + other inputs)
    all_inputs = inputs + music_inputs + sfx_inputs

    # ── Build filter_complex ──
    filters: list[str] = []

    # Step 1: Scale each bonded segment to canvas size.
    #
    # PR #83 — when a bonded segment carries pip_layout metadata other
    # than "fullscreen", the segment is treated as a corner overlay
    # rather than a full-canvas fill. We scale the baked clip down to
    # the configured PIP size, build a rounded-rectangle alpha mask
    # (with feather softness on the edges) and a drop shadow, then
    # overlay onto a black base sized to the canvas. The resulting
    # composite is the segment that flows into the concat step, so
    # downstream stages don't need to know about PIP — they keep
    # seeing canvas-sized segments in temporal order.
    for i, (v_label, a_label) in enumerate(segment_labels):
        seg = bonded_segments[i] if i < len(bonded_segments) else {}
        seg_meta = (seg.get("metadata") or {}) if isinstance(seg, dict) else {}
        pip_layout = seg_meta.get("pip_layout") or "fullscreen"

        # Non-mic-on blocks get the lo-fi "recorded on a phone" VO texture;
        # mic-on blocks keep the clean studio voice. The chain is applied to
        # this segment's intrinsic audio before it flows into the concat.
        #
        # aresample=async=1000 stretches/squeezes each segment's audio by up
        # to 1000 samples/s to keep its timestamps locked to the video. Without
        # it, a per-clip A/V container mismatch of even ~50ms accumulates across
        # the concat and reads as lipsync drift that snaps back at each cut.
        if phone_mic_filter_enabled() and not block_is_mic_on(seg_meta):
            seg_a_filter = f"{phone_mic_filter_chain()},aresample=async=1000"
        else:
            seg_a_filter = "aresample=async=1000"

        # Geometry + edge treatment — single source of truth lives in
        # services.timeline_builder / layouts.primitives so the renderer +
        # frontend stay in lockstep. We delay the import to module-call time
        # because cast_ffmpeg_composer is reused in places where services/
        # isn't on the path during static analysis. Coercing here keeps the
        # composer working both pre- and post-migration (legacy strings like
        # ``pip_small`` / ``top_half`` still resolve to a primitive).
        from layouts.primitives import LayoutPrimitive, coerce_to_primitive  # noqa: WPS433
        from services.timeline_builder import pip_geometry  # noqa: WPS433

        primitive = coerce_to_primitive(pip_layout)

        if primitive == LayoutPrimitive.SPLIT_H.value:
            # split_h face half is a full-bleed rectangle (no rounded window).
            # Scale the baked clip to the face half and overlay it onto a
            # canvas-sized black base so the concat step sees a canvas frame.
            placement = pip_geometry(pip_layout, canvas_width, canvas_height)
            half_w = max(2, int(placement.get("w", 0)))
            half_h = max(2, int(placement.get("h", 0)))
            half_x = int(placement.get("x", 0))
            half_y = int(placement.get("y", 0))
            base = f"pip{i}_split_base"
            scaled = f"pip{i}_split_scaled"
            filters.append(
                f"[{i}:v]scale={half_w}:{half_h}:force_original_aspect_ratio=increase,"
                f"crop={half_w}:{half_h},setsar=1[{scaled}]"
            )
            filters.append(
                f"color=c=black:s={canvas_width}x{canvas_height}:d=1[{base}]"
            )
            filters.append(
                f"[{base}][{scaled}]overlay=x={half_x}:y={half_y}[{v_label}]"
            )
            filters.append(f"[{i}:a]{seg_a_filter}[{a_label}]")
            continue

        if primitive in (
            LayoutPrimitive.PIP_QUARTER_BL.value,
            LayoutPrimitive.PIP_QUARTER_BR.value,
        ):
            placement = pip_geometry(pip_layout, canvas_width, canvas_height)
            pip_w = max(2, int(placement.get("w", 0)))
            pip_h = max(2, int(placement.get("h", 0)))
            pip_x = int(placement.get("x", 0))
            pip_y = int(placement.get("y", 0))
            radius = max(0, int(placement.get("border_radius", 0)))
            feather = max(0, int(placement.get("feather", 0)))
            shadow = placement.get("drop_shadow") or {}
            shadow_blur = max(0, int(shadow.get("blur", 0)))
            shadow_alpha = max(0.0, min(1.0, float(shadow.get("alpha", 0.0))))

            # Per-segment intermediate labels. We keep them namespaced
            # by segment index so the filter graph stays unambiguous
            # when many PIP blocks are present.
            scaled = f"pip{i}_scaled"
            mask = f"pip{i}_mask"
            masked = f"pip{i}_masked"
            shadow_alpha_lbl = f"pip{i}_shadow_a"
            shadow_lbl = f"pip{i}_shadow"
            base_with_shadow = f"pip{i}_base_shadow"
            base = f"pip{i}_base"

            # 1. Scale baked clip to PIP square, force SAR=1 so the
            #    overlay lands without aspect-ratio drift.
            filters.append(
                f"[{i}:v]scale={pip_w}:{pip_h}:force_original_aspect_ratio=increase,"
                f"crop={pip_w}:{pip_h},setsar=1[{scaled}]"
            )

            # 2. Build a soft rounded-rectangle alpha mask. geq paints
            #    per-pixel: full white inside the rounded rect, falling
            #    linearly across the `feather` band, fully transparent
            #    outside. The distance metric is the rounded-rect SDF
            #    `hypot(max(0,|x-cx|-(W/2-r)), max(0,|y-cy|-(H/2-r)))`
            #    — negative-equivalent (i.e. inside the rect by more
            #    than the radius) at the center, equal to `r` at the
            #    exact rounded edge, and increasing into the feathered
            #    falloff and beyond. We clamp to [0,1] then scale to
            #    [0,255] for the gray channel; the inverted sense
            #    (inside = high) keeps the rect opaque.
            radius_safe = max(1, radius)
            feather_safe = max(1, feather)
            # In filter_complex, commas inside filter args must be
            # escaped as \\, so geq sees the literal comma instead of
            # treating it as the filter-chain delimiter.
            d_expr = (
                f"hypot(max(0\\,abs(X-{pip_w}/2)-({pip_w}/2-{radius_safe}))"
                f"\\,max(0\\,abs(Y-{pip_h}/2)-({pip_h}/2-{radius_safe})))"
            )
            mask_expr = (
                f"255*clip(({radius_safe}-{d_expr})/{feather_safe}\\,0\\,1)"
            )
            filters.append(
                f"color=c=black:s={pip_w}x{pip_h},format=gray,"
                f"geq=lum='{mask_expr}'[{mask}]"
            )

            # 3. Apply the mask as the alpha channel of the scaled clip.
            filters.append(
                f"[{scaled}][{mask}]alphamerge[{masked}]"
            )

            # 4. Build the canvas-sized base. Drop-shadow is a blurred
            #    copy of the mask, alpha-scaled, overlaid at +6 px
            #    offset (135° / lower-right per Adobe's PIP convention).
            filters.append(
                f"color=c=black:s={canvas_width}x{canvas_height}:d=1[{base}]"
            )
            if shadow_blur > 0 and shadow_alpha > 0:
                # Shadow is just the mask itself, blurred + alpha-scaled,
                # overlaid as a dark layer at the PIP offset.
                filters.append(
                    f"[{mask}]gblur=sigma={shadow_blur},"
                    f"format=gray,geq=lum='val*{shadow_alpha}'[{shadow_alpha_lbl}]"
                )
                filters.append(
                    f"color=c=black:s={pip_w}x{pip_h}[{shadow_lbl}_src]"
                )
                filters.append(
                    f"[{shadow_lbl}_src][{shadow_alpha_lbl}]alphamerge[{shadow_lbl}]"
                )
                filters.append(
                    f"[{base}][{shadow_lbl}]overlay=x={pip_x + 6}:y={pip_y + 6}[{base_with_shadow}]"
                )
                base_for_pip = base_with_shadow
            else:
                base_for_pip = base

            # 5. Composite the masked PIP onto the base. The resulting
            #    canvas-sized stream becomes this segment's v_label,
            #    which the concat step consumes unchanged.
            filters.append(
                f"[{base_for_pip}][{masked}]overlay=x={pip_x}:y={pip_y}[{v_label}]"
            )
            filters.append(f"[{i}:a]{seg_a_filter}[{a_label}]")
            continue

        # Default (fullscreen) — preserve legacy behaviour exactly.
        filters.append(
            f"[{i}:v]scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_width}:{canvas_height}:(ow-iw)/2:(oh-ih)/2,setsar=1[{v_label}]"
        )
        filters.append(f"[{i}:a]{seg_a_filter}[{a_label}]")

    # Step 2: Concat all bonded segments
    if segment_labels:
        concat_v_inputs = "".join(f"[{v}][{a}]" for v, a in segment_labels)
        n = len(segment_labels)
        filters.append(f"{concat_v_inputs}concat=n={n}:v=1:a=1[timeline_v][timeline_a]")
    else:
        # Fallback: generate a black canvas with silence
        filters.append(
            f"color=c=black:s={canvas_width}x{canvas_height}:d=5[timeline_v];"
            f"anullsrc=cl=stereo:r=44100[timeline_a]"
        )

    current_v_label = "timeline_v"

    # Step 3b: Apply non-bonded video overlays (stock track).
    #
    # These inputs were declared above but, prior to this step, were never
    # composited — the stock track silently dropped out of the render. We
    # scale each overlay to its element geometry, shift its PTS to the
    # element's absolute timeline start so it plays at the right moment, then
    # overlay it onto the running video with an `enable` window bounding it to
    # [start, end]. eof_action=pass keeps the timeline intact if an overlay
    # clip is shorter than its window. Geometry comes from el["props"]
    # (x/y/width/height) — matching extract_overlay_elements output — not from
    # el["frame"]. Video overlays composite UNDER captions but OVER the avatar,
    # so this runs after the bonded concat and before the caption layer.
    for idx, (label, el) in enumerate(overlay_video_labels):
        real_idx = next(
            j for j, inp in enumerate(all_inputs) if inp.label == label
        )
        props = el.get("props", {}) or {}
        x = props.get("x", 0)
        y = props.get("y", 0)
        w = props.get("width", canvas_width)
        h = props.get("height", canvas_height)
        start = el.get("s", 0)
        end = el.get("e", 5)
        out_label = f"ov_vid_{idx}"
        # Cover-fit (object-fit: cover): scale to fill the box, then centre-crop
        # the overflow. A plain `scale=w:h` here stretched b-roll whose source
        # aspect didn't match the box — e.g. a portrait Pexels clip on a 16:9
        # cast came out horizontally squished.
        filters.append(
            f"[{real_idx}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[{label}_scaled]"
        )
        filters.append(
            f"[{label}_scaled]setpts=PTS-STARTPTS+{start}/TB[{label}_shifted]"
        )
        filters.append(
            f"[{current_v_label}][{label}_shifted]overlay=x={x}:y={y}"
            f":enable='between(t,{start},{end})':eof_action=pass[{out_label}]"
        )
        current_v_label = out_label

    # Step 3: Apply image overlays
    for idx, (label, el) in enumerate(overlay_image_labels):
        input_number = len(inputs) - len(overlay_image_labels) + idx  # position in all_inputs (before music)
        # Actually we need the correct index...
        # Find the real input index for this label
        real_idx = next(
            j for j, inp in enumerate(all_inputs) if inp.label == label
        )
        frame = el.get("frame", {})
        x = frame.get("x", 0)
        y = frame.get("y", 0)
        w = frame.get("width", 200)
        h = frame.get("height", 200)
        start = el.get("s", 0)
        end = el.get("e", 5)
        out_label = f"ov_img_{idx}"
        filters.append(
            f"[{real_idx}:v]scale={w}:{h}[{label}_scaled]"
        )
        filters.append(
            f"[{current_v_label}][{label}_scaled]overlay=x={x}:y={y}"
            f":enable='between(t,{start},{end})'[{out_label}]"
        )
        current_v_label = out_label

    # Step 4: Apply drawtext captions — Caption Fix 3 Phase A.
    #
    # Each caption element carries Remotion-style word-level tokens in
    # props._captions_tokens (each {text, startMs, endMs}). We group those
    # tokens into pages of <= props.pageDurationInMilliseconds (matches
    # the preview's createTikTokStyleCaptions behaviour) and emit ONE
    # drawtext filter per page with that page's enable window. Style
    # values come from the same preset that powers the preview — font,
    # weight, size, color, stroke, position, optional box backdrop — so
    # preview ≈ render. On top of the page layer we stack one extra
    # drawtext per word with the preset's highlightColor that is enabled
    # only during that token's [startMs, endMs], reproducing the
    # word-by-word karaoke highlight from caption-page.tsx.
    # Step 12: SSR caption overlay (preferred when CAPTIONS_SSR_ENABLED).
    # For each caption block we ask the SSR service to render the EXACT preview
    # overlay (PNG sequence / alpha video) and alpha-composite it here, instead
    # of approximating the animation with stacked drawtext filters. The SSR
    # output dirs we create are tracked so the caller can clean them up after
    # the render. Any per-block SSR failure falls through to the drawtext path
    # below for that block — captions never block the render.
    ssr_enabled = _captions_ssr_enabled()
    ssr_overlay_dirs: list[str] = []

    cap_filter_idx = 0
    for cap in caption_elements:
        props = cap.get("props", {}) or {}
        cap_start_s = float(cap.get("s", 0) or 0)
        cap_end_s = float(cap.get("e", cap_start_s + 1) or cap_start_s + 1)

        if ssr_enabled:
            new_label = _try_ssr_caption_overlay(
                cap=cap,
                props=props,
                cap_start_s=cap_start_s,
                cap_end_s=cap_end_s,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                all_inputs=all_inputs,
                filters=filters,
                current_v_label=current_v_label,
                ssr_overlay_dirs=ssr_overlay_dirs,
            )
            if new_label is not None:
                # SSR overlay composited successfully — skip the drawtext path
                # for this block so we don't double-burn the captions.
                current_v_label = new_label
                continue

        font_size = int(props.get("fontSize") or _caption_default_font_size())
        # White base text by default (TikTok style); the element may still
        # override with its own color/fontColor.
        font_color = (
            props.get("color")
            or props.get("fontColor")
            or _caption_default_base_color()
        )
        # Active-word color used for the karaoke-style highlight pass below.
        # The editor stores it on the caption item (see editorStarterMapping
        # ts; props.highlightColor is forwarded along with .color). When the
        # field is absent we fall back to the bright default highlight so the
        # karaoke pass always pops against the base text.
        highlight_color = (
            props.get("highlightColor")
            or props.get("highlight_color")
            or props.get("activeWordColor")
            or _caption_default_highlight_color()
        )
        stroke_color = props.get("strokeColor") or _caption_default_stroke_color()
        try:
            stroke_width = max(0, int(props.get("strokeWidth") or _caption_default_stroke_width()))
        except (TypeError, ValueError) as e:
            sentry_sdk.capture_exception(e)
            stroke_width = _caption_default_stroke_width()
        font_family = (
            props.get("fontFamily") or _caption_default_font_family()
        ).strip() or _caption_default_font_family()
        try:
            font_weight = int(props.get("fontWeight") or 700)
        except (TypeError, ValueError) as e:
            sentry_sdk.capture_exception(e)
            font_weight = 700
        font_file = _resolve_caption_font_file(font_family, font_weight)
        page_ms = int(props.get("pageDurationInMilliseconds") or 3500)
        if page_ms < 500:
            page_ms = 500

        # Optional FFmpeg-only box backdrop (Pill / Block Quote presets).
        box_enabled = bool(props.get("ffmpegBoxEnabled"))
        box_color = props.get("ffmpegBoxColor") or "black@0.5"

        # Y position: prefer explicit `top` (in canvas pixels) from the
        # editor; otherwise look at the preset's positionY fraction;
        # otherwise use `frame.y`; otherwise default to the bottom-third
        # anchor (~30% up from the bottom, rule of thirds).
        default_y = int(canvas_height * _caption_default_position_y())
        position_y_frac = props.get("positionY")
        if props.get("top") is not None:
            try:
                y_pos = max(0, int(props["top"]))
            except (TypeError, ValueError) as e:
                sentry_sdk.capture_exception(e)
                y_pos = default_y
        elif position_y_frac is not None:
            try:
                frac = float(position_y_frac)
                y_pos = max(0, int(canvas_height * frac))
            except (TypeError, ValueError) as e:
                sentry_sdk.capture_exception(e)
                y_pos = default_y
        else:
            frame = cap.get("frame", {}) or {}
            y_pos = int(frame.get("y", default_y))

        # Optional uppercase / lowercase transform (e.g. ALL CAPS preset).
        text_transform = (props.get("textTransform") or "none").lower()

        def _xform(s: str) -> str:
            if text_transform == "uppercase":
                return s.upper()
            if text_transform == "lowercase":
                return s.lower()
            return s

        def _box_args() -> str:
            if not box_enabled:
                return ""
            return f"box=1:boxcolor={box_color}:boxborderw=12:"

        tokens = props.get("_captions_tokens") or []
        pages = _group_caption_tokens_into_pages(tokens, page_ms)
        # regr-2c: re-split any time-windowed page whose text would overflow
        # maxLines at this font size / width so the tail words can't crop.
        # Bug A: clamp the usable width to the horizontal safe area
        # (canvas - 2 * absolute safe_inset) so a line can never reach the
        # cropping edge on any frame type, and wrap each page on word
        # boundaries using a measured PIL font instead of a character guess.
        safe_caption_width = _caption_safe_width(canvas_width)
        requested_width = props.get("captionWidth")
        if requested_width:
            caption_width = min(int(requested_width), safe_caption_width)
        else:
            caption_width = safe_caption_width
        # Default to a SINGLE caption line — a longer caption is re-split into
        # one-line pages timed to the words (the second line shows only once
        # the first is spoken). Explicit props.maxLines still wins.
        max_lines = int(props.get("maxLines") or 1)
        measure_font = _caption_font(font_file, font_size)
        budget = _chars_per_line(font_size, caption_width) * max(1, max_lines)
        capped_pages: list[dict] = []
        for page in pages:
            if len(page["text"]) <= budget:
                capped_pages.append(page)
                continue
            capped_pages.extend(
                cap_tokens_to_line_budget(
                    page["tokens"],
                    font_size=font_size,
                    caption_width=caption_width,
                    max_lines=max_lines,
                )
            )
        pages = capped_pages

        if not pages:
            # Legacy single-string fallback so old casts still render. Wrap it
            # against the safe width too (Round-6 Bug A) so a long single string
            # can't clip at the edge.
            raw_text = _xform(props.get("text", "") or "")
            wrapped_lines = _wrap_text_to_width(
                raw_text, measure_font, float(caption_width)
            )
            text = "\n".join(_ffmpeg_escape_drawtext(ln) for ln in wrapped_lines)
            if not text:
                continue
            out_label = f"cap_{cap_filter_idx}"
            filters.append(
                f"[{current_v_label}]drawtext="
                f"fontfile='{font_file}':"
                f"text='{text}':"
                f"fontsize={font_size}:"
                f"fontcolor={font_color}:"
                f"{_box_args()}"
                f"line_spacing=6:fix_bounds=1:"
                f"borderw={stroke_width}:bordercolor={stroke_color}:"
                f"x=(w-text_w)/2:y={y_pos}:"
                f"enable='between(t,{cap_start_s},{cap_end_s})'"
                f"[{out_label}]"
            )
            current_v_label = out_label
            cap_filter_idx += 1
            continue

        for page in pages:
            page_text_raw = _xform(page["text"])
            if not page_text_raw.strip():
                continue
            # Round-6 Bug A: wrap on word boundaries against the measured safe
            # width so no rendered line reaches the crop edge. Each wrapped line
            # is escaped independently and joined with a real newline (drawtext
            # treats a raw \n in text= as a hard line break).
            wrapped_lines = _wrap_text_to_width(
                page_text_raw, measure_font, float(caption_width)
            )
            if not wrapped_lines:
                continue
            max_line_px = max(
                (_measure_text_width(ln, measure_font) for ln in wrapped_lines),
                default=0.0,
            )
            logger.info(
                "[caption-fit] text_len=%d chars=%d lines=%d max_line_px=%.1f "
                "safe_width=%d canvas_width=%d font_size=%d",
                len(page_text_raw),
                sum(len(ln) for ln in wrapped_lines),
                len(wrapped_lines),
                max_line_px,
                caption_width,
                canvas_width,
                font_size,
            )
            page_text = "\n".join(
                _ffmpeg_escape_drawtext(ln) for ln in wrapped_lines
            )
            if not page_text:
                continue
            page_start_s = page["start_ms"] / 1000.0
            page_end_s = page["end_ms"] / 1000.0
            # Clamp page window to the caption element's own [s, e] so we
            # never burn text outside the block.
            page_start_s = max(page_start_s, cap_start_s)
            page_end_s = min(page_end_s, cap_end_s)
            if page_end_s <= page_start_s:
                continue

            # Base page layer — full text in the preset's base color.
            out_label = f"cap_{cap_filter_idx}"
            filters.append(
                f"[{current_v_label}]drawtext="
                f"fontfile='{font_file}':"
                f"text='{page_text}':"
                f"fontsize={font_size}:"
                f"fontcolor={font_color}:"
                f"{_box_args()}"
                f"line_spacing=6:fix_bounds=1:"
                f"borderw={stroke_width}:bordercolor={stroke_color}:"
                f"x=(w-text_w)/2:y={y_pos}:"
                f"enable='between(t,{page_start_s:.3f},{page_end_s:.3f})'"
                f"[{out_label}]"
            )
            current_v_label = out_label
            cap_filter_idx += 1

            # Karaoke-style highlight: overlay each token in highlight_color
            # for that token's [startMs, endMs] window. drawtext can't paint
            # a sub-string of an existing text run, so we stack a per-token
            # drawtext on top of the base page text. The x offset is a
            # character-width approximation (font_size * 0.55 per char) so
            # the highlighted word lands roughly above its base-text
            # counterpart. Skip when the user kept the default and
            # highlight_color == font_color — there is nothing to overlay.
            if highlight_color and highlight_color != font_color:
                page_tokens = page.get("tokens") or []
                full_text = page.get("text") or ""
                # Page text is centered; estimate horizontal offset of each
                # token's first char from the page's left edge.
                char_w = font_size * 0.55
                cursor = 0
                for tok in page_tokens:
                    tok_text = (tok.get("text") or "").strip()
                    if not tok_text:
                        continue
                    tok_start_s = max(int(tok.get("startMs", 0)) / 1000.0, page_start_s)
                    tok_end_s = min(int(tok.get("endMs", 0)) / 1000.0, page_end_s)
                    if tok_end_s <= tok_start_s:
                        cursor += len(tok_text) + 1
                        continue
                    # Offset of this token's center from the page's center,
                    # in pixels (approximate).
                    page_len = max(len(full_text), 1)
                    tok_center_chars = cursor + (len(tok_text) / 2.0)
                    page_center_chars = page_len / 2.0
                    px_offset = (tok_center_chars - page_center_chars) * char_w
                    escaped_tok = _ffmpeg_escape_drawtext(tok_text)
                    if not escaped_tok:
                        cursor += len(tok_text) + 1
                        continue
                    hl_label = f"cap_{cap_filter_idx}"
                    sign = "+" if px_offset >= 0 else "-"
                    filters.append(
                        f"[{current_v_label}]drawtext="
                        f"fontfile='{font_file}':"
                        f"text='{escaped_tok}':"
                        f"fontsize={font_size}:"
                        f"fontcolor={highlight_color}:"
                        f"borderw={stroke_width}:bordercolor={stroke_color}:"
                        f"x=(w-text_w)/2{sign}{abs(px_offset):.1f}:y={y_pos}:"
                        f"enable='between(t,{tok_start_s:.3f},{tok_end_s:.3f})'"
                        f"[{hl_label}]"
                    )
                    current_v_label = hl_label
                    cap_filter_idx += 1
                    cursor += len(tok_text) + 1  # +1 for the space separator

    # Step 4b: Compose product carousels.
    # Each carousel group becomes one video stream that overlays the
    # canvas for its time window. We allocate fresh input indices for the
    # carousel's images/videos starting AFTER all_inputs.
    if carousel_groups:
        composer = CastFFmpegComposer(canvas_width=canvas_width, canvas_height=canvas_height)
        next_input_idx = len(all_inputs)
        for gid, items in carousel_groups.items():
            try:
                # Build one _CarouselInputSpec per item from the timeline
                # element. items are already sorted by carousel_index.
                first = items[0]
                first_meta = first.get("metadata") or {}
                transition = str(first_meta.get("carousel_transition") or "crossfade")
                speed_default = float(first_meta.get("carousel_speed_seconds") or 3.0)

                specs: list[_CarouselInputSpec] = []
                for el in items:
                    em = el.get("metadata") or {}
                    speed = float(em.get("carousel_speed_seconds") or speed_default)
                    src = (el.get("props") or {}).get("src") or ""
                    if not src:
                        continue
                    specs.append(_CarouselInputSpec(
                        src=src,
                        media_type=el.get("type") or "image",
                        speed_s=speed,
                    ))
                if len(specs) < 2:
                    # Defensive: _group_carousel_elements should already
                    # have collapsed degenerate groups, but if every spec
                    # had an empty src we end up here. Skip the group;
                    # the timeline will simply have no product layer.
                    continue

                input_args, filter_lines, out_label = composer._compose_carousel_block(
                    group_id=gid,
                    items=specs,
                    transition=transition,
                    ffmpeg_input_offset=next_input_idx,
                )

                # Register each carousel input as a real FFmpegInput so
                # build_ffmpeg_command emits it. _compose_carousel_block
                # returned per-input arg lists shaped like
                # ["-loop", "1", "-t", "3.5", "-i", src] or
                # ["-stream_loop", "-1", "-t", "3.5", "-an", "-i", src].
                # We split that into pre_input_args (everything before -i)
                # and the URL itself (after -i).
                for i, args in enumerate(input_args):
                    if "-i" in args:
                        i_pos = args.index("-i")
                        pre = args[:i_pos]
                        src = args[i_pos + 1] if i_pos + 1 < len(args) else ""
                    else:
                        pre, src = [], args[-1]
                    inp_label = f"car{gid.replace('-', '_')}_{i}"
                    all_inputs.append(FFmpegInput(
                        url=src,
                        label=inp_label,
                        has_video=True,
                        # Videos in the carousel had `-an` baked into their
                        # input flags; flag accordingly so callers don't
                        # try to grab audio they explicitly stripped.
                        has_audio=False,
                        pre_input_args=pre,
                    ))
                next_input_idx += len(input_args)

                filters.extend(filter_lines)

                # Overlay the carousel output onto the canvas for the
                # group's time window. Start = first item's `s`, end =
                # last item's `e` (already includes the 0.5s overhang).
                overlay_start_s = float(items[0].get("s") or 0.0)
                overlay_end_s = float(items[-1].get("e") or overlay_start_s + 1.0)
                overlay_label = f"car{gid.replace('-', '_')}_overlaid"
                # The carousel output is already canvas-sized (the inner
                # scale+pad pre-filter saw to that), so a plain overlay
                # at 0,0 is enough.
                filters.append(
                    f"[{current_v_label}][{out_label}]overlay=0:0:"
                    f"enable='between(t,{overlay_start_s:.3f},{overlay_end_s:.3f})'"
                    f"[{overlay_label}]"
                )
                current_v_label = overlay_label
            except Exception as exc:
                # Carousel composition is non-essential — fall back to no
                # carousel rather than failing the whole render. The block
                # will simply lose its product layer for this take.
                sentry_sdk.capture_exception(exc)
                logger.warning(
                    "Failed to compose carousel for group %s: %s. Skipping.",
                    gid, exc,
                )

    final_v = current_v_label

    # Step 5: Music sidechain ducking
    if music_labels:
        # Find the real input index for music
        music_input_indices = [
            next(j for j, inp in enumerate(all_inputs) if inp.label == ml)
            for ml in music_labels
        ]

        # Split narration for sidechain
        filters.append(f"[timeline_a]asplit[narration_main][narration_sidechain]")

        music_volume, music_volume_source = resolve_music_volume(
            music_elements, cast_volume_override=cast_music_volume
        )
        # Single definitive log line at the moment the music volume is committed
        # into the filtergraph. Fires on every render that has music so we can
        # always answer "what volume did this render actually use, and why?".
        logger.info(
            "[music-mix] render=%s volume=%.6f source=%s n_music_elements=%d "
            "default=%.6f env=%s cast_override=%s",
            render_id,
            music_volume,
            music_volume_source,
            len(music_elements),
            DEFAULT_MUSIC_VOLUME,
            os.getenv("MUSIC_DEFAULT_VOLUME"),
            cast_music_volume,
        )

        # Mix all music inputs, apply volume reduction
        if len(music_labels) == 1:
            midx = music_input_indices[0]
            filters.append(f"[{midx}:a]volume={music_volume}[music_raw]")
        else:
            for i, midx in enumerate(music_input_indices):
                filters.append(f"[{midx}:a]volume={music_volume}[mus_vol_{i}]")
            mus_mix_in = "".join(f"[mus_vol_{i}]" for i in range(len(music_labels)))
            filters.append(f"{mus_mix_in}amix=inputs={len(music_labels)}:duration=longest[music_raw]")

        # EQ the music bus to carve room for the voice before ducking: a
        # 50 Hz high-pass clears sub rumble and a -2 dB high-shelf at 10 kHz
        # softens the highs that compete with VO sibilance.
        filters.append(
            f"[music_raw]highpass=f=50,highshelf=g=-2:f=10000[music_eq]"
        )

        # Sidechain compress: duck music when narration is present. Relaxed
        # from threshold=0.03:ratio=12 — those settings pumped audibly (the bed
        # "breathed" under speech). 0.05:ratio=6 ducks cleanly without the pump.
        filters.append(
            f"[music_eq][narration_sidechain]sidechaincompress="
            f"threshold=0.05:ratio=6:attack=10:release=300[music_ducked]"
        )
        # normalize=0 so the voice keeps its level; amix's default 1/N
        # normalisation would otherwise drop narration ~6 dB and let the bed
        # creep up relative to it.
        filters.append(
            f"[narration_main][music_ducked]amix=inputs=2:duration=longest:"
            f"normalize=0[final_a]"
        )
        final_a = "final_a"
    else:
        final_a = "timeline_a"

    # Step 6: SFX overlay — delayed to each cue, mixed at full volume on top of
    # the (possibly music-ducked) narration. SFX are accents, NOT ducked: they
    # should punch through. amix would attenuate by 1/N, so we sum and cap with
    # an explicit normalize=0 instead.
    if sfx_specs:
        sfx_input_indices = {
            label: next(j for j, inp in enumerate(all_inputs) if inp.label == label)
            for label, _, _ in sfx_specs
        }
        sfx_mix_labels: list[str] = []
        for i, (label, start_s, volume) in enumerate(sfx_specs):
            sidx = sfx_input_indices[label]
            delay_ms = int(round(start_s * 1000))
            out_label = f"sfx_d{i}"
            filters.append(
                f"[{sidx}:a]adelay={delay_ms}|{delay_ms},volume={volume}[{out_label}]"
            )
            sfx_mix_labels.append(out_label)

        sfx_mix_in = f"[{final_a}]" + "".join(f"[{lbl}]" for lbl in sfx_mix_labels)
        filters.append(
            f"{sfx_mix_in}amix=inputs={len(sfx_mix_labels) + 1}:duration=first:normalize=0[final_a_sfx]"
        )
        final_a = "final_a_sfx"

    filter_complex = ";\n".join(filters)

    output_key = f"renders/{render_id}/final.mp4"

    return CompositionPlan(
        inputs=all_inputs,
        filter_complex=filter_complex,
        output_map=[f"[{final_v}]", f"[{final_a}]"],
        output_key=output_key,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        ssr_overlay_dirs=ssr_overlay_dirs,
    )


def build_ffmpeg_command(
    plan: CompositionPlan,
    input_files: dict[str, str],
    output_path: str,
    use_nvenc: bool = True,
) -> list[str]:
    """Build the final FFmpeg command from a composition plan.

    Args:
        plan: The composition plan
        input_files: Mapping of input labels to local file paths
        output_path: Local output file path
        use_nvenc: Use h264_nvenc (GPU) or libx264 (CPU fallback)

    Returns:
        FFmpeg command as a list of strings
    """
    cmd = ["ffmpeg", "-y"]

    # Add inputs in order. Per-input pre-flags (e.g. -loop, -stream_loop,
    # -t) come BEFORE this input's -i so they bind to it; FFmpeg input
    # options are positional this way.
    for inp in plan.inputs:
        local_path = input_files.get(inp.label, inp.url)
        if inp.pre_input_args:
            cmd.extend(list(inp.pre_input_args))
        cmd.extend(["-i", local_path])

    # Filter complex
    cmd.extend(["-filter_complex", plan.filter_complex])

    # Output maps
    for m in plan.output_map:
        cmd.extend(["-map", m])

    # Encoding settings
    if use_nvenc:
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-cq", "20",
            "-b:v", "4000k",
            "-maxrate", "5000k",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-b:v", "4000k",
            "-maxrate", "5000k",
        ])

    cmd.extend([
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.1",
        "-movflags", "+faststart",
        output_path,
    ])

    return cmd


# ── Product carousel composition ──────────────────────────────────────────────
#
# A carousel group is a set of timeline elements with the same
# metadata.carousel_group_id (== the source block_id). Each element points
# at one product asset (image or video) and carries:
#   - carousel_index: 0..N-1 (display order within the group)
#   - carousel_total: N
#   - carousel_transition: "crossfade" | "slide" | "zoom"
#   - carousel_speed_seconds: float, seconds per slot
#
# The composer collapses all items in a group into a single video stream by
# chaining `xfade` filters between consecutive inputs. The resulting stream
# starts at the group's first element.s and runs for
# (group_total * speed) seconds.

@dataclass
class _CarouselInputSpec:
    """One input within a carousel group (image or video)."""
    src: str
    media_type: str        # "image" or "video"
    speed_s: float          # how long this slot is fully visible


class CastFFmpegComposer:
    """Stateful helper for non-trivial FFmpeg composition fragments.

    Today its sole responsibility is product carousel composition; the
    main `translate_timeline_to_ffmpeg` flow stays as a module-level
    function but reaches into this class when it spots carousel-group
    metadata. Future composer work (multi-track ducking, picture-in-
    picture trees) can live here too.
    """

    def __init__(self, canvas_width: int = 1080, canvas_height: int = 1920) -> None:
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height

    def _compose_carousel_block(
        self,
        group_id: str,
        items: list[_CarouselInputSpec],
        transition: str,
        ffmpeg_input_offset: int,
    ) -> tuple[list[list[str]], list[str], str]:
        """Build the FFmpeg input args + filter graph fragment for one carousel.

        Args:
            group_id: stable id used to namespace filter labels (== block_id).
            items: ordered list of carousel slots. items[0] anchors the chain;
                each subsequent item xfade-overlaps onto the running graph.
            transition: friendly transition name ("crossfade"/"slide"/"zoom").
            ffmpeg_input_offset: index of the FIRST input this fragment owns
                in the final FFmpeg argv. Each item gets one `-i`, so input
                indices are ffmpeg_input_offset .. ffmpeg_input_offset + N-1.

        Returns:
            (input_args, filter_lines, output_label) where:
              input_args is a list of arg lists (one per input) ready to be
                flattened into the ffmpeg argv,
              filter_lines is a list of filter strings (joined with ';' by
                the caller),
              output_label is the name of the carousel's final video stream.

        Notes / gotchas:
          - Images need `-loop 1 -t {speed+0.5}` to become a video stream of
            the right duration. Videos pass through with `-an` so we drop
            their audio (the cast voiceover is the audio source for the
            block; product clip audio would conflict).
          - Short videos (< speed_s) are padded by `-stream_loop -1` then
            cut to the slot length.
          - All inputs are scale+pad'd to the canvas dimensions before
            xfade — xfade requires identical SAR / dims on both sides or
            it errors out at filter init.
          - xfade chain: for N inputs, produce N-1 chained xfades. The
            offset for the i-th xfade (i >= 1) = i*speed - 0.5 — that is
            half a second before slot i's full display, so the fade window
            sits inside slot i-1's overhang.
        """
        if not items:
            return [], [], ""
        xfade_name = XFADE_MAP.get(transition, "fade")

        input_args: list[list[str]] = []
        for it in items:
            # Each slot needs the carousel's `speed + 0.5s` of source frames
            # so the next xfade's 0.5s window has material to fade out from.
            slot_with_overhang = float(it.speed_s) + _CAROUSEL_XFADE_DURATION_S
            if it.media_type == "video":
                # Loop short videos so we never run out of frames mid-slot;
                # `-t` clips to the exact slot length. `-an` drops audio
                # because the carousel video must not fight the cast voice.
                input_args.append([
                    "-stream_loop", "-1",
                    "-t", f"{slot_with_overhang:.3f}",
                    "-an",
                    "-i", it.src,
                ])
            else:
                # Image → video stream of the slot length.
                input_args.append([
                    "-loop", "1",
                    "-t", f"{slot_with_overhang:.3f}",
                    "-i", it.src,
                ])

        # Normalise every input to canvas dims + 1:1 SAR. xfade is fussy.
        # Each input ends up labelled `[c{group}_norm_{i}]`.
        gid = group_id.replace("-", "_")
        filter_lines: list[str] = []
        norm_labels: list[str] = []
        for i in range(len(items)):
            input_idx = ffmpeg_input_offset + i
            norm_label = f"c{gid}_norm_{i}"
            filter_lines.append(
                f"[{input_idx}:v]"
                f"scale={self.canvas_width}:{self.canvas_height}:force_original_aspect_ratio=decrease,"
                f"pad={self.canvas_width}:{self.canvas_height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,format=yuva420p"
                f"[{norm_label}]"
            )
            norm_labels.append(norm_label)

        # Chain the xfades. With N inputs we emit N-1 xfade nodes; for
        # N == 1 the only output is the normalised first input.
        if len(items) == 1:
            return input_args, filter_lines, norm_labels[0]

        prev_label = norm_labels[0]
        out_label = ""
        for i in range(1, len(items)):
            slot_speed = float(items[i - 1].speed_s)
            # Fade window sits in the overlap region: starts 0.5s before
            # the full display of slot i ends, lasts 0.5s. Slot i begins
            # at i * slot_speed (uniform speed). Hence offset = i*speed - 0.5.
            offset = max(0.0, i * slot_speed - _CAROUSEL_XFADE_DURATION_S)
            out_label = f"c{gid}_x{i}"
            filter_lines.append(
                f"[{prev_label}][{norm_labels[i]}]"
                f"xfade=transition={xfade_name}"
                f":duration={_CAROUSEL_XFADE_DURATION_S:.3f}"
                f":offset={offset:.3f}"
                f"[{out_label}]"
            )
            prev_label = out_label

        return input_args, filter_lines, out_label


@dataclass
class ClipSegment:
    """One trimmed window of the parent's composed mp4."""
    start_s: float
    end_s: float


def build_clip_filter_complex(segments: list[ClipSegment]) -> str:
    """Build an FFmpeg filter_complex that trims `segments` from input 0 and
    concats them in order. Jump cuts only — no transitions between segments
    in v1 (the spec leaves a hook for crossfade in a follow-up; see
    `clip_transition` on the metadata dict).

    Each segment ends with `setpts=PTS-STARTPTS` per the spec — that
    rebases each trimmed segment to t=0 so the concat filter doesn't see
    discontinuous timestamps.
    """
    if not segments:
        raise ValueError("build_clip_filter_complex: no segments")
    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, seg in enumerate(segments):
        parts.append(
            f"[0:v]trim=start={seg.start_s:.3f}:end={seg.end_s:.3f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={seg.start_s:.3f}:end={seg.end_s:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")
    parts.append(
        "".join(concat_inputs) + f"concat=n={len(segments)}:v=1:a=1[vout][aout]"
    )
    return ";".join(parts)


def _resolve_clip_segments(
    parent_timeline: dict | None,
    block_ids: list[str],
    *,
    default_fps: int = 30,
) -> list[ClipSegment]:
    """Convert parent timeline + child clip_block_ids into FFmpeg trim windows.

    The parent timeline_snapshot is a Twick "tracks → elements" tree. We
    look across every track for the bonded element whose metadata.block_id
    matches each requested block_id, and use its `s` (start, seconds) and
    `e` (end, seconds) fields as the trim bounds. Elements without `s/e`
    (some Twick variants store frames-from-start instead) fall back to
    `from / durationInFrames` in frames at `default_fps`.

    Block IDs that aren't found in the parent timeline are skipped with a
    warning — better to compose what we can than abort the whole clip.
    """
    if not parent_timeline or not block_ids:
        return []
    # Build a lookup: block_id → (s, e) seconds.
    by_block: dict[str, tuple[float, float]] = {}
    tracks = parent_timeline.get("tracks") or []
    for track in tracks:
        for el in track.get("elements", []) or []:
            meta = el.get("metadata") or {}
            bid = meta.get("block_id")
            if not bid or not meta.get("bonded"):
                continue
            if "s" in el and "e" in el:
                start_s = float(el.get("s") or 0.0)
                end_s = float(el.get("e") or 0.0)
            elif "from" in el and "durationInFrames" in el:
                fps = int(parent_timeline.get("fps") or default_fps) or default_fps
                start_s = float(el["from"]) / fps
                end_s = (float(el["from"]) + float(el["durationInFrames"])) / fps
            else:
                continue
            if end_s <= start_s:
                continue
            # First occurrence wins — bonded V1 is the canonical avatar
            # timeline; any other reference would be a cosmetic overlay.
            by_block.setdefault(bid, (start_s, end_s))

    segments: list[ClipSegment] = []
    for bid in block_ids:
        if bid not in by_block:
            logger.warning("clip compose: block_id %s not found in parent timeline", bid)
            continue
        s, e = by_block[bid]
        segments.append(ClipSegment(start_s=s, end_s=e))
    return segments


async def compose_clip_from_parent(
    *,
    parent_video_input: str,
    block_ids: list[str],
    parent_timeline: dict | None,
    output_path: str,
    default_fps: int = 30,
) -> dict:
    """Compose a child cast's mp4 by FFmpeg-trimming the parent's composed mp4.

    `parent_video_input` is anything FFmpeg's `-i` accepts — a local path
    or a presigned URL. R2 supports range requests so passing a presigned
    URL streams what's needed without a full download.

    Returns metadata::

        {
          "output_path": "...",
          "duration_seconds": 23.5,
          "segments": [{"start_s": 0.0, "end_s": 8.2}, ...],
        }

    Raises RuntimeError on FFmpeg failure or when no block IDs resolve.
    Caller is responsible for uploading `output_path` to R2 and persisting
    the resulting URL on the child cast.
    """
    import asyncio
    segments = _resolve_clip_segments(parent_timeline, block_ids, default_fps=default_fps)
    if not segments:
        raise RuntimeError("No segments resolved for clip; aborting compose")

    filter_complex = build_clip_filter_complex(segments)

    # NB: no hard timeout per project rules — long clips with many
    # segments must not be cut off.
    cmd = [
        "ffmpeg", "-y",
        "-i", parent_video_input,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    logger.info("clip compose: %d segments → %s", len(segments), output_path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"FFmpeg clip compose failed: {tail}")
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise

    duration = sum(seg.end_s - seg.start_s for seg in segments)
    return {
        "output_path": output_path,
        "duration_seconds": round(duration, 3),
        "segments": [{"start_s": s.start_s, "end_s": s.end_s} for s in segments],
    }


def _group_carousel_elements(elements: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Split a flat element list into (non-carousel, {group_id: [items]}).

    Items are bucketed by metadata.carousel_group_id and sorted within each
    group by carousel_index. Groups with fewer than 2 items are flattened
    back to non-carousel — a degenerate carousel is just a static overlay.
    """
    plain: list[dict] = []
    groups: dict[str, list[dict]] = {}
    for el in elements:
        meta = el.get("metadata") or {}
        gid = meta.get("carousel_group_id")
        if gid:
            groups.setdefault(gid, []).append(el)
        else:
            plain.append(el)
    # Sort each group by carousel_index; degenerate groups (size 1) become
    # plain overlays so we don't emit a no-op xfade.
    finalised: dict[str, list[dict]] = {}
    for gid, items in groups.items():
        items.sort(key=lambda e: int((e.get("metadata") or {}).get("carousel_index", 0)))
        if len(items) < 2:
            plain.extend(items)
        else:
            finalised[gid] = items
    return plain, finalised
