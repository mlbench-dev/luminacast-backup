"""Talking-head layout geometry (regression-5: four canonical primitives).

The talking-head V1 element is positioned according to the block's layout
primitive (see ``layouts.primitives.LayoutPrimitive``):

  fullscreen     — avatar fills the canvas
  split_h        — face occupies one horizontal half; the other half is
                   product / b-roll content (see compute_geometry)
  pip_quarter_bl — face scaled to ~1/4 canvas area, bottom-LEFT, safe gutter
  pip_quarter_br — same, bottom-RIGHT

``hidden`` is preserved as an orthogonal no-FACE state (V1 omitted; audio
still plays via A1).

The raw rectangles come from ``layouts.primitives.compute_geometry`` — the
single source of truth. This module wraps that with the renderer-facing edge
treatment (the quarter-PIP windows get a rounded edge + drop shadow):

  border_radius=24, feather=12, drop_shadow={blur:10, alpha:0.5}

Both the frontend timeline mapping and the renderer compose path read the
same dict so a tweak lands everywhere at once.
"""
from __future__ import annotations

from typing import Optional, TypedDict

import sentry_sdk

from layouts.primitives import (
    LayoutPrimitive,
    coerce_to_primitive,
    compute_geometry,
)

# Reference canvas. The constants below are sized for 1080×1920 portrait;
# `pip_geometry` scales position / size to whatever canvas the caller
# provides so a future 1440×2560 cast lands at the same visual fraction.
REFERENCE_CANVAS_W = 1080
REFERENCE_CANVAS_H = 1920

# Margin from the top-left corner of the safe area. Twenty-four pixels at
# 1080 px wide tracks the social-commerce convention (Adobe Premiere uses
# the same 25-percent perimeter inset; we're slightly tighter so the head
# sits inside TikTok's 160 px top reserved zone).
SAFE_MARGIN_PX = 24

# Edge treatment shared by every PIP layout. These keys are read by the
# FFmpeg compose stage to apply the rounded-rect alpha mask + soft edge +
# drop shadow before overlaying the baked clip onto the base canvas.
PIP_BORDER_RADIUS = 24
PIP_FEATHER = 12
PIP_DROP_SHADOW = {"blur": 10, "alpha": 0.5}

# Bake resolution for any non-fullscreen PIP layout. MuseTalk handles
# small face crops well; baking at 480×480 keeps GPU time low and matches
# the largest PIP size the user can pick so we never upscale.
PIP_BAKE_SIZE = 480


class PipPlacement(TypedDict, total=False):
    """Geometry + edge treatment for a single bonded V1 element.

    ``visible`` is False for the ``hidden`` layout — callers should omit
    the V1 element entirely from the timeline but keep A1 so the audio
    still plays.
    """
    visible: bool
    x: int
    y: int
    w: int
    h: int
    border_radius: int
    feather: int
    drop_shadow: dict
    pip_layout: str
    bake_size: int


def _coerce_layout(value: object) -> str:
    """Coerce an arbitrary value to a canonical layout string.

    Delegates to ``layouts.primitives.coerce_to_primitive`` — accepts the four
    primitives (``fullscreen`` / ``split_h`` / ``pip_quarter_bl`` /
    ``pip_quarter_br``), the preserved ``hidden`` state, and every legacy value
    (``full`` / ``top_half`` / ``bottom_half`` / ``pip_small`` / ``pip_medium``
    / ``pip_small_bl`` / ``pip_small_br``). Anything malformed degrades to
    ``fullscreen`` with a Sentry capture.
    """
    return coerce_to_primitive(value)


def pip_geometry(
    pip_layout: object,
    canvas_width: int = REFERENCE_CANVAS_W,
    canvas_height: int = REFERENCE_CANVAS_H,
    opts: Optional[dict] = None,
) -> PipPlacement:
    """Return geometry + edge treatment for a block's layout primitive.

    The geometry itself comes from ``layouts.primitives.compute_geometry`` (the
    single source of truth); this wrapper adds the renderer-facing edge
    treatment (border radius / feather / drop shadow) and bake size that the
    FFmpeg compose stage consumes.

    Args:
        pip_layout: a ``LayoutPrimitive``, canonical string, or any legacy /
            arbitrary value (degrades to fullscreen).
        canvas_width / canvas_height: output canvas size; defaults match the
            1080×1920 portrait reference.
        opts: forwarded to ``compute_geometry`` (e.g. ``split_face_position``).

    Returns:
        A ``PipPlacement`` dict. ``fullscreen`` covers the whole canvas with no
        edge treatment. ``hidden`` sets ``visible=False`` (caller drops V1).
        ``split_h`` returns the FACE half as x/y/w/h with no rounding (it's a
        full-bleed half, not a floating window). ``pip_quarter_*`` carries the
        corner placement plus rounded-edge treatment.
    """
    try:
        canvas_w = int(canvas_width or REFERENCE_CANVAS_W)
        canvas_h = int(canvas_height or REFERENCE_CANVAS_H)
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        canvas_w, canvas_h = REFERENCE_CANVAS_W, REFERENCE_CANVAS_H

    geo = compute_geometry(pip_layout, canvas_w, canvas_h, opts)
    layout = geo["primitive"]

    if not geo["visible"] or geo["face_rect"] is None:
        return {
            "visible": False,
            "x": 0,
            "y": 0,
            "w": 0,
            "h": 0,
            "border_radius": 0,
            "feather": 0,
            "drop_shadow": {},
            "pip_layout": layout,
            "bake_size": PIP_BAKE_SIZE,
        }

    face = geo["face_rect"]

    if layout == LayoutPrimitive.FULLSCREEN.value:
        return {
            "visible": True,
            "x": face.x,
            "y": face.y,
            "w": face.w,
            "h": face.h,
            "border_radius": 0,
            "feather": 0,
            "drop_shadow": {},
            "pip_layout": layout,
            "bake_size": 0,
        }

    # split_h face half is a full-bleed rectangle (no floating-window edge).
    if layout == LayoutPrimitive.SPLIT_H.value:
        return {
            "visible": True,
            "x": face.x,
            "y": face.y,
            "w": face.w,
            "h": face.h,
            "border_radius": 0,
            "feather": 0,
            "drop_shadow": {},
            "pip_layout": layout,
            "bake_size": PIP_BAKE_SIZE,
        }

    # pip_quarter_bl / pip_quarter_br — floating rounded window.
    return {
        "visible": True,
        "x": face.x,
        "y": face.y,
        "w": face.w,
        "h": face.h,
        "border_radius": PIP_BORDER_RADIUS,
        "feather": PIP_FEATHER,
        "drop_shadow": dict(PIP_DROP_SHADOW),
        "pip_layout": layout,
        "bake_size": PIP_BAKE_SIZE,
    }


# ── Template `face` → canonical primitive mapping ──────────────────────────
#
# A layout-template preset's ``config.face`` is now one of the four primitives
# (``fullscreen`` / ``split_h`` / ``pip_quarter_bl`` / ``pip_quarter_br``) or
# the preserved ``hidden`` state after the regression-5 migration. Legacy
# values (``full`` / ``top_half`` / ``pip_small`` / ``pip_medium`` …) still
# resolve via the alias table so the model works both pre- and post-migration.
def face_to_pip_layout(face: object) -> str:
    """Map a template ``config.face`` value to a canonical layout string.

    Returns one of the four primitives or ``hidden``. Unknown / malformed
    input degrades to ``fullscreen`` (with a Sentry capture inside
    ``coerce_to_primitive``) so bad template data never breaks a render.
    """
    return coerce_to_primitive(face)


def is_pip_layout(pip_layout: object) -> bool:
    """True when the layout needs a non-fullscreen bake (anything but
    ``fullscreen``): ``split_h``, ``pip_quarter_bl``, ``pip_quarter_br``, or
    ``hidden``.

    Renderer uses this to switch the bake to a smaller crop via MuseTalk and to
    apply the rounded-edge compose treatment for the quarter-PIP primitives.
    """
    layout = _coerce_layout(pip_layout)
    return layout != LayoutPrimitive.FULLSCREEN.value


def resolve_block_pip_layout(block, template_face: object = None) -> str:
    """Resolve a block's effective pip_layout.

    Resolution order (Step 4):
      1. The block's own ``block_metadata['pip_layout']`` — a block-level
         override ALWAYS wins (the user set it explicitly).
      2. Otherwise the cast's template ``face`` mapped via
         ``face_to_pip_layout`` (the template default).
      3. Otherwise ``fullscreen`` (legacy default).

    ``template_face`` is the raw ``config.face`` string from the cast's
    layout template (or ``None`` when no template is attached / the flag is
    off). The block-override check is on the RAW metadata value, not the
    coerced one, so a block that set ``fullscreen`` explicitly still beats a
    template that says ``hidden``.
    """
    try:
        meta = getattr(block, "block_metadata", None) or {}
        if isinstance(meta, dict):
            raw = meta.get("pip_layout")
            if raw is not None and str(raw).strip() != "":
                return _coerce_layout(raw)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)

    if template_face is not None:
        return face_to_pip_layout(template_face)
    return LayoutPrimitive.FULLSCREEN.value


def apply_pip_to_v1_element(
    v1_element: dict,
    pip_layout: object,
    canvas_width: int = REFERENCE_CANVAS_W,
    canvas_height: int = REFERENCE_CANVAS_H,
) -> Optional[dict]:
    """Mutate a V1 element dict to reflect the block's pip_layout.

    Returns the mutated element on success, or ``None`` when the layout
    is ``hidden`` (the caller MUST drop the element from the timeline so
    the audio still plays without a visible face). For fullscreen this
    is a no-op (the element keeps whatever x/y/w/h it had).

    The element shape matches the timeline snapshot:
        {
            "id": "v1_<block_id>",
            "type": "video" | "image",
            "props": {"x": int, "y": int, "width": int, "height": int,
                      "borderRadius": int, ...},
            "metadata": {..., "pip_layout": str},
            ...
        }
    """
    placement = pip_geometry(pip_layout, canvas_width, canvas_height)
    layout = placement["pip_layout"]

    if not placement.get("visible"):
        return None

    props = v1_element.setdefault("props", {})
    meta = v1_element.setdefault("metadata", {})

    if layout == LayoutPrimitive.FULLSCREEN.value:
        meta["pip_layout"] = layout
        # Strip stale PIP styling so a block flipped back to fullscreen
        # doesn't keep a half-applied rounded edge.
        for k in ("feather", "drop_shadow"):
            meta.pop(k, None)
        props["borderRadius"] = 0
        return v1_element

    props["x"] = placement["x"]
    props["y"] = placement["y"]
    props["width"] = placement["w"]
    props["height"] = placement["h"]
    props["borderRadius"] = placement["border_radius"]
    meta["pip_layout"] = layout
    meta["feather"] = placement["feather"]
    meta["drop_shadow"] = placement["drop_shadow"]
    return v1_element
