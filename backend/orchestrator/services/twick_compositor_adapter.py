"""Translate Twick timeline JSON into compositor inputs.

Walks all tracks/elements from a Twick timeline and routes them
by type into text_overlays, product_overlays, and music_track
structures that the video_compositor can consume.
"""

import logging
import os
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)

# Default canvas dimensions (9:16 portrait)
CANVAS_WIDTH = 720
CANVAS_HEIGHT = 1280

# ── Step 4: overlay-slot placement from a layout template ──────────────────
#
# A layout-template preset's ``config.overlay`` carries:
#   anchor     — where the overlay box sits (enum below)
#   width_frac — overlay box width as a fraction of canvas width (0..1)
#   margin     — inset from the anchored edge(s), in REFERENCE-canvas pixels
#                (scaled to the actual canvas height so it tracks the design
#                intent across resolutions)
#
# resolve_overlay_placement turns that into absolute pixel geometry for the
# given canvas. When no template is attached we keep the historical
# bottom-third default (the same 0.78 vertical fraction the FFmpeg caption
# burn already uses), so behaviour is unchanged for casts without a template.

# Anchors understood by resolve_overlay_placement. Unknown anchors degrade to
# bottom_center (the product default) with a Sentry capture.
_OVERLAY_ANCHORS = {
    "bottom_center",
    "bottom_left",
    "bottom_right",
    "top_center",
}

# Reference canvas for the template `margin` value (matches the 1080×1920
# design canvas used across the seeder + timeline_builder).
_OVERLAY_REFERENCE_H = 1920

# Default vertical placement (fraction of canvas height) when no template is
# attached — the existing bottom-third caption position. Env-overridable so
# the fallback can be retuned without a code change.
def _default_overlay_y_frac() -> float:
    raw = os.getenv("OVERLAY_DEFAULT_Y_FRAC")
    if not raw:
        return 0.78
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        return 0.78
    return max(0.0, min(val, 1.0))


def resolve_overlay_placement(
    template: object = None,
    canvas_width: int = 1080,
    canvas_height: int = 1920,
) -> dict:
    """Resolve the overlay slot's pixel geometry.

    Args:
        template: A ``LayoutTemplate`` row (or any object/dict exposing a
            ``config`` mapping with an ``overlay`` block), or ``None``. When
            ``None`` — or when the template has no usable overlay config — we
            return the historical bottom-third default so casts without a
            template render exactly as before.
        canvas_width / canvas_height: Output canvas size in pixels. Defaults
            match the 1080×1920 portrait design canvas; pass the cast's real
            canvas (honouring existing canvas detection) for other formats.

    Returns:
        ``{source, anchor, x, y, width, margin}`` — ``x``/``y`` are the
        TOP-LEFT pixel of the overlay box, ``width`` its pixel width.
        ``source`` is ``"template"`` when the geometry came from the
        template, ``"default"`` for the bottom-third fallback.
    """
    try:
        cw = int(canvas_width or 1080)
        ch = int(canvas_height or 1920)
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        cw, ch = 1080, 1920

    overlay_cfg = _overlay_config_of(template)
    if not overlay_cfg:
        # No template attached → bottom-third default. Width matches the
        # seeder default (0.28 of canvas) so the box size is sensible even
        # without a template.
        width = int(round(0.28 * cw))
        x = int(round((cw - width) / 2))
        y = int(round(ch * _default_overlay_y_frac()))
        return {
            "source": "default",
            "anchor": "bottom_center",
            "x": x,
            "y": y,
            "width": width,
            "margin": 0,
        }

    anchor = str(overlay_cfg.get("anchor") or "bottom_center").strip().lower()
    if anchor not in _OVERLAY_ANCHORS:
        sentry_sdk.capture_exception(ValueError(f"unknown overlay anchor {anchor!r}"))
        anchor = "bottom_center"

    try:
        width_frac = float(overlay_cfg.get("width_frac", 0.28))
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        width_frac = 0.28
    width_frac = max(0.0, min(width_frac, 1.0))

    try:
        margin_ref = float(overlay_cfg.get("margin", 0))
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        margin_ref = 0.0
    # margin is specified in reference-canvas px; scale to the real canvas
    # height so the inset tracks the design intent across resolutions.
    margin = int(round(max(0.0, margin_ref) * ch / _OVERLAY_REFERENCE_H))

    width = int(round(width_frac * cw))

    # Horizontal placement.
    if anchor == "bottom_left":
        x = margin
    elif anchor == "bottom_right":
        x = max(0, cw - width - margin)
    else:  # bottom_center / top_center
        x = int(round((cw - width) / 2))

    # Vertical placement: top_center hugs the top edge (margin down), every
    # bottom_* anchor sits a margin up from the bottom edge. We do not know
    # the overlay's own height here (it depends on the rendered content), so
    # `y` is the anchored EDGE the caller lays content against.
    if anchor == "top_center":
        y = margin
    else:
        y = max(0, ch - margin)

    return {
        "source": "template",
        "anchor": anchor,
        "x": x,
        "y": y,
        "width": width,
        "margin": margin,
    }


def _overlay_config_of(template: object) -> Optional[dict]:
    """Extract the ``config.overlay`` dict from a template-like object.

    Accepts a ``LayoutTemplate`` ORM row, a plain dict, or ``None``. Returns
    the overlay dict, or ``None`` when nothing usable is present.
    """
    if template is None:
        return None
    try:
        if isinstance(template, dict):
            config = template.get("config")
        else:
            config = getattr(template, "config", None)
        if not isinstance(config, dict):
            return None
        overlay = config.get("overlay")
        return overlay if isinstance(overlay, dict) else None
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return None

# Base product-overlay card width (px) — must match the default used by
# services.video_compositor.composite_product_overlays_multi.
PRODUCT_OVERLAY_BASE_WIDTH = 300

# PRODUCT_DEMO blocks render a generic bottle in the avatar's hand even
# with a stronger prompt, so we enlarge the real-product overlay that sits
# on top to hide more of the generic bake. Scale is operator-tunable via
# PRODUCT_DEMO_OVERLAY_SCALE (default 1.25); clamped to [1.0, 1.5] so the
# card never grows large enough to obscure the avatar's face.
_PRODUCT_DEMO_OVERLAY_SCALE_DEFAULT = 1.25
_PRODUCT_DEMO_OVERLAY_SCALE_MIN = 1.0
_PRODUCT_DEMO_OVERLAY_SCALE_MAX = 1.5


def product_demo_overlay_scale() -> float:
    """Return the PRODUCT_DEMO product-overlay scale factor.

    Reads ``PRODUCT_DEMO_OVERLAY_SCALE`` (default 1.25) and clamps it to
    [1.0, 1.5]. A malformed value falls back to the default rather than
    raising — this runs inside the render pipeline.
    """
    raw = os.environ.get("PRODUCT_DEMO_OVERLAY_SCALE")
    if raw is None or raw.strip() == "":
        return _PRODUCT_DEMO_OVERLAY_SCALE_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _PRODUCT_DEMO_OVERLAY_SCALE_DEFAULT
    return max(
        _PRODUCT_DEMO_OVERLAY_SCALE_MIN,
        min(val, _PRODUCT_DEMO_OVERLAY_SCALE_MAX),
    )


def product_demo_overlay_width() -> int:
    """Scaled product-overlay card width (px) for PRODUCT_DEMO blocks."""
    return int(round(PRODUCT_OVERLAY_BASE_WIDTH * product_demo_overlay_scale()))


def _element_midpoint_seconds(element: dict) -> float:
    """Return the midpoint time of an element in seconds."""
    s = element.get("s", 0)
    e = element.get("e", s)
    return (s + e) / 2.0


def _find_block_for_time(block_regions: list[dict], time_s: float) -> Optional[int]:
    """Return the block index whose time region contains time_s.

    block_regions is a list of {block_id, start_s, end_s, index}.
    Returns the index (position) of the matching block, or None.
    """
    for region in block_regions:
        start = region.get("start_s", 0)
        end = region.get("end_s", float("inf"))
        if start <= time_s <= end:
            return region.get("index", 0)
    return None


def translate_timeline_to_overlays(
    twick_data: dict,
    block_regions: list[dict],
    blocks: list = None,
) -> dict:
    """Parse Twick timeline JSON into per-block compositor inputs.

    Args:
        twick_data: The full Twick timeline dict with 'tracks' list.
        block_regions: List of {block_id, start_s, end_s, index} mapping
                       time ranges to block positions.
        blocks: Optional list of Block ORM objects for metadata lookup.

    Returns:
        {
            "text_overlays": {block_index: [overlay, ...]},
            "product_overlays": {block_index: [overlay, ...]},
            "music_track": {"src": url, "track_id": id} or None,
        }
    """
    text_overlays: dict[int, list] = {}
    product_overlays: dict[int, list] = {}
    music_track: Optional[dict] = None

    if not twick_data:
        return {
            "text_overlays": text_overlays,
            "product_overlays": product_overlays,
            "music_track": music_track,
        }

    tracks = twick_data.get("tracks", [])

    # Identify voice/video track IDs to exclude from music detection
    voice_video_track_ids = set()
    for track in tracks:
        track_id = track.get("id", "")
        elements = track.get("elements", [])
        for el in elements:
            el_type = el.get("type", "")
            if el_type in ("video", "audio"):
                # If it's a video element, it's the main track
                if el_type == "video":
                    voice_video_track_ids.add(track_id)
                # For audio: check if this looks like voice (same track as video, or explicit)
                # Heuristic: if audio src contains 'tts' or 'voice', it's voice
                src = (el.get("props") or {}).get("src", "")
                if "tts" in src.lower() or "voice" in src.lower():
                    voice_video_track_ids.add(track_id)

    for track in tracks:
        track_id = track.get("id", "")
        elements = track.get("elements", [])

        for el in elements:
            el_type = el.get("type", "")
            props = el.get("props") or {}
            start_s = el.get("s", 0)
            end_s = el.get("e", start_s)
            midpoint = _element_midpoint_seconds(el)
            block_idx = _find_block_for_time(block_regions, midpoint)
            if block_idx is None:
                block_idx = 0  # fallback to first block

            if el_type == "text":
                text_content = props.get("text", "")
                if not text_content:
                    continue

                # Position resolution order:
                # 1. element.position.x/y (Twick TextElement serialization)
                # 2. element.frame.x/y (fallback for image/video-style elements)
                # 3. Default: horizontally centered, near top
                # Note: Twick only serializes position when user moves the element.
                # If absent, the element is at default canvas position.
                pos = el.get("position") or {}
                frame = el.get("frame") or {}
                x = pos.get("x") or frame.get("x") or (CANVAS_WIDTH // 2)
                y = pos.get("y") or frame.get("y") or 100

                overlay = {
                    "text": text_content,
                    "start_s": start_s,
                    "end_s": end_s,
                    "x": x,
                    "y": y,
                    "color": props.get("fill", "white"),
                    "font_size": props.get("fontSize", 48),
                    "font_family": props.get("fontFamily", "Sans"),
                    "font_weight": props.get("fontWeight", "normal"),
                    "font_style": props.get("fontStyle", "normal"),
                    "stroke_color": props.get("stroke", ""),
                    "stroke_width": props.get("lineWidth", 0),
                    "text_align": props.get("textAlign", "center"),
                    "text_effect": el.get("textEffect"),
                }
                text_overlays.setdefault(block_idx, []).append(overlay)

            elif el_type == "image":
                metadata = el.get("metadata") or {}
                product_id = metadata.get("product_id")
                if not product_id:
                    continue  # skip non-product images

                frame = el.get("frame") or {}
                overlay = {
                    "product_id": product_id,
                    "src": props.get("src", ""),
                    "start_s": start_s,
                    "end_s": end_s,
                    "x": frame.get("x", 0),
                    "y": frame.get("y", 0),
                    "object_fit": el.get("objectFit", "cover"),
                }
                product_overlays.setdefault(block_idx, []).append(overlay)

            elif el_type == "audio":
                el_meta = el.get("metadata") or {}
                # Bonded audio IS the narration (paired with a baked video
                # element); never treat it as music even if it lands on a
                # non-voice track id.
                if el_meta.get("bonded"):
                    continue
                # SFX accents are mixed by the ffmpeg composer's dedicated SFX
                # path — never treat one as the background-music track.
                if el_meta.get("kind") == "sfx":
                    continue
                # An explicit music element (auto-placed by auto_arrange) wins
                # outright; otherwise fall back to the track-id heuristic
                # (a non-bonded audio element on a non-voice/video track).
                is_music = el_meta.get("kind") == "music" or track_id not in voice_video_track_ids
                if is_music:
                    src = props.get("src", "")
                    if src and music_track is None:
                        vol = props.get("volume", el_meta.get("volume"))
                        music_track = {
                            "src": src,
                            "track_id": track_id,
                            "start_s": start_s,
                            "end_s": end_s,
                            "volume": vol,
                        }

    logger.info(
        "Translated timeline: %d text overlay blocks, %d product overlay blocks, music=%s",
        len(text_overlays),
        len(product_overlays),
        bool(music_track),
    )

    return {
        "text_overlays": text_overlays,
        "product_overlays": product_overlays,
        "music_track": music_track,
    }


# Default PIP placement (bottom-right area of 720x1280 canvas)
DEFAULT_PIP_X = 480
DEFAULT_PIP_Y = 900
DEFAULT_PIP_WIDTH = 200
DEFAULT_PIP_HEIGHT = 280


def get_pip_placement_for_block(
    timeline_json: dict,
    block_id: str,
    block_regions: list[dict],
) -> tuple[int, int, int, int]:
    """Walk timeline tracks looking for a PIP avatar element for the given block.

    A PIP avatar element is identified by metadata.is_pip_avatar == True.
    Returns (x, y, width, height) from the element frame, or the default
    placement if no PIP avatar element is found.

    Args:
        timeline_json: The full Twick timeline dict with 'tracks' list.
        block_id: The block ID to find PIP placement for.
        block_regions: List of {block_id, start_s, end_s, index} mappings.

    Returns:
        Tuple of (x, y, width, height) in pixels.
    """
    if not timeline_json:
        return (DEFAULT_PIP_X, DEFAULT_PIP_Y, DEFAULT_PIP_WIDTH, DEFAULT_PIP_HEIGHT)

    # Find the time range for this block
    block_start = None
    block_end = None
    for region in block_regions:
        if str(region.get("block_id")) == str(block_id):
            block_start = region.get("start_s", 0)
            block_end = region.get("end_s", float("inf"))
            break

    tracks = timeline_json.get("tracks", [])
    for track in tracks:
        for el in track.get("elements", []):
            metadata = el.get("metadata") or {}
            if not metadata.get("is_pip_avatar"):
                continue

            # Check if this element overlaps with the block time range
            el_start = el.get("s", 0)
            el_end = el.get("e", el_start)

            if block_start is not None:
                midpoint = (el_start + el_end) / 2.0
                if midpoint < block_start or midpoint > block_end:
                    continue

            # Found a matching PIP avatar element
            frame = el.get("frame") or {}
            x = int(frame.get("x", DEFAULT_PIP_X))
            y = int(frame.get("y", DEFAULT_PIP_Y))
            w = int(frame.get("width", DEFAULT_PIP_WIDTH))
            h = int(frame.get("height", DEFAULT_PIP_HEIGHT))
            return (x, y, w, h)

    return (DEFAULT_PIP_X, DEFAULT_PIP_Y, DEFAULT_PIP_WIDTH, DEFAULT_PIP_HEIGHT)


def translate_timeline_to_overlays_v2(
    twick_data: dict,
    block_regions: list[dict],
    blocks: list = None,
) -> dict:
    """Extended version of translate_timeline_to_overlays that also extracts
    video_segments for user-added video clips with NLE cuts.

    Returns the same dict as translate_timeline_to_overlays plus:
        "video_segments": {block_index: [segment, ...]}
    """
    base = translate_timeline_to_overlays(twick_data, block_regions, blocks)

    video_segments: dict[int, list] = {}

    if not twick_data:
        base["video_segments"] = video_segments
        return base

    tracks = twick_data.get("tracks", [])
    for track in tracks:
        for el in track.get("elements", []):
            el_type = el.get("type", "")
            if el_type != "video":
                continue

            metadata = el.get("metadata") or {}
            # Skip avatar clips — only process user-added video elements
            if metadata.get("is_avatar"):
                continue

            props = el.get("props") or {}
            src = props.get("src", "")
            if not src:
                continue

            start_s = el.get("s", 0)
            end_s = el.get("e", start_s)
            midpoint = (start_s + end_s) / 2.0

            block_idx = _find_block_for_time(block_regions, midpoint)
            if block_idx is None:
                block_idx = 0

            # NLE trim offset: Twick uses mediaOffset or trimStart
            media_offset = float(
                el.get("mediaOffset", el.get("trimStart", 0))
            )

            # Find block region to compute relative times
            block_start_s = 0
            block_end_s = float("inf")
            for region in block_regions:
                if region.get("index", 0) == block_idx:
                    block_start_s = region.get("start_s", 0)
                    block_end_s = region.get("end_s", float("inf"))
                    break

            video_segments.setdefault(block_idx, []).append({
                "src": src,
                "media_offset": media_offset,
                "start_s_in_block": max(0, start_s - block_start_s),
                "end_s_in_block": min(end_s - block_start_s, block_end_s - block_start_s),
            })

    base["video_segments"] = video_segments

    # regression-5: SPLIT_H content half. For blocks whose layout primitive is
    # split_h, the half NOT occupied by the face is the canonical home for
    # product / b-roll content. When such a block carries a stock-video URL or a
    # product-overlay URL, emit a video/image element occupying the content_rect
    # so the downstream compose step (Step 6) lays the media into that half.
    split_h_overlays = _build_split_h_content_overlays(block_regions, blocks)
    if split_h_overlays:
        for block_idx, overlay in split_h_overlays.items():
            product_overlays = base.setdefault("product_overlays", {})
            product_overlays.setdefault(block_idx, []).insert(0, overlay)

    # regr-wiring: stock_video blocks have no avatar bake — the entire frame is
    # the stock clip. Emit a fullscreen visual overlay so the otherwise-black
    # canvas is filled with the block's stock media for its whole slot.
    stock_video_overlays = _build_stock_video_fullscreen_overlays(block_regions, blocks)
    if stock_video_overlays:
        for block_idx, overlay in stock_video_overlays.items():
            product_overlays = base.setdefault("product_overlays", {})
            product_overlays.setdefault(block_idx, []).insert(0, overlay)

    logger.info(
        "Translated timeline v2: %d video segment blocks, %d split_h content halves, "
        "%d fullscreen stock_video overlays",
        len(video_segments),
        len(split_h_overlays),
        len(stock_video_overlays),
    )
    return base


def _block_content_source(block) -> Optional[tuple[str, str]]:
    """Return ``(kind, url)`` for a block's product / b-roll content, or None.

    Prefers an explicit product-overlay URL, then a stock-media URL. ``kind`` is
    ``"image"`` for photos / product stills and ``"video"`` for stock footage so
    the compose step picks the right element type.
    """
    try:
        product_url = getattr(block, "product_overlay_url", None)
        if product_url:
            return ("image", str(product_url))

        stock_url = getattr(block, "stock_media_url", None)
        if stock_url:
            stock_kind = (getattr(block, "stock_media_kind", None) or "").strip().lower()
            kind = "video" if stock_kind == "video" else "image"
            return (kind, str(stock_url))
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
    return None


def _build_split_h_content_overlays(
    block_regions: list[dict],
    blocks: list = None,
) -> dict[int, dict]:
    """Map block_index → a content overlay dict for every split_h block that
    carries a stock-video or product-overlay URL.

    The overlay occupies the SPLIT_H content_rect (the half not holding the
    face), derived from ``layouts.primitives.compute_geometry`` so the geometry
    stays in lockstep with the renderer.
    """
    overlays: dict[int, dict] = {}
    if not blocks:
        return overlays

    try:
        from layouts.primitives import (
            LayoutPrimitive,
            coerce_to_primitive,
            compute_geometry,
            split_face_position_for_legacy,
        )
        from services.timeline_builder import resolve_block_pip_layout
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return overlays

    blocks_by_id = {str(getattr(b, "id", "")): b for b in blocks}

    for region in block_regions:
        block_id = str(region.get("block_id", ""))
        block = blocks_by_id.get(block_id)
        if block is None:
            continue

        try:
            raw_layout = resolve_block_pip_layout(block)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            continue

        if coerce_to_primitive(raw_layout) != LayoutPrimitive.SPLIT_H.value:
            continue

        source = _block_content_source(block)
        if source is None:
            continue
        kind, url = source

        face_pos = split_face_position_for_legacy(raw_layout)
        geo = compute_geometry(
            LayoutPrimitive.SPLIT_H.value,
            CANVAS_WIDTH,
            CANVAS_HEIGHT,
            {"split_face_position": face_pos},
        )
        content = geo.get("content_rect")
        if content is None:
            continue

        block_idx = region.get("index", 0)
        overlays[block_idx] = {
            "src": url,
            "kind": kind,
            "x": content.x,
            "y": content.y,
            "width": content.w,
            "height": content.h,
            "object_fit": "cover",
            "source": "split_h_content",
        }

    return overlays


def _build_stock_video_fullscreen_overlays(
    block_regions: list[dict],
    blocks: list = None,
) -> dict[int, dict]:
    """Map block_index → a fullscreen overlay dict for every ``stock_video``
    block that carries a stock-media URL.

    A ``stock_video`` block has no avatar bake: the entire frame is the stock
    clip. Without a fullscreen overlay the composited canvas stays black for
    that slot. The overlay fills the full canvas (origin 0,0) so the compose
    step lays the media across the whole frame for the block's time window.

    Idempotent / best-effort: a block with no usable URL is skipped silently
    (no overlay, no error). Every failure is Sentry-captured and swallowed so a
    single bad block never blocks the render.
    """
    overlays: dict[int, dict] = {}
    if not blocks:
        return overlays

    blocks_by_id = {str(getattr(b, "id", "")): b for b in blocks}

    for region in block_regions:
        block_id = str(region.get("block_id", ""))
        block = blocks_by_id.get(block_id)
        if block is None:
            continue

        try:
            category = (getattr(block, "category", None) or "").strip().lower()
            if category != "stock_video":
                continue

            stock_url = getattr(block, "stock_media_url", None)
            if not stock_url:
                # No media to lay down — skip silently (no overlay, no error).
                continue

            stock_kind = (getattr(block, "stock_media_kind", None) or "").strip().lower()
            kind = "image" if stock_kind == "image" else "video"

            block_idx = region.get("index", 0)
            overlays[block_idx] = {
                "src": str(stock_url),
                "kind": kind,
                "x": 0,
                "y": 0,
                "width": CANVAS_WIDTH,
                "height": CANVAS_HEIGHT,
                "object_fit": "cover",
                "source": "stock_video_fullscreen",
            }
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            continue

    return overlays
