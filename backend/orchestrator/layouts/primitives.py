"""Canonical layout primitives (regression-5).

Replaces the ad-hoc layout vocabulary (``full`` / ``top_half`` / ``bottom_half``
/ ``pip_small`` / ``pip_medium`` / ``pip_small_bl`` / ``pip_small_br``) with FOUR
canonical primitives that describe where the talking face sits on the canvas:

  fullscreen      — avatar fills the whole canvas; no separate content region.
  split_h         — two horizontal halves. One half is the avatar, the other is
                    product / b-roll content. Which half holds the face is
                    configurable per block via ``split_face_position`` ("top" |
                    "bottom", default "top").
  pip_quarter_bl  — talking face scaled to ~1/4 of the canvas AREA, anchored to
                    the BOTTOM-LEFT corner with a safe-area margin.
  pip_quarter_br  — same quarter-area face, anchored to the BOTTOM-RIGHT corner.

This module is the SINGLE SOURCE OF TRUTH for the geometry. ``compute_geometry``
returns face/content rectangles for any canvas; ``timeline_builder`` and the
renderer delegate here so a future tweak lands everywhere at once. It supersedes
the older layout vocabulary documented in ``Luminacast_Sequenced_Build.md``.

``hidden`` is intentionally NOT one of the four primitives: it is a no-FACE
state (audio plays, no avatar layer) rather than a face-layout. It is preserved
as an orthogonal value so voiceover renders keep working — see
``LEGACY_LAYOUT_ALIASES`` and ``coerce_to_primitive``.
"""
from __future__ import annotations

import enum
from typing import NamedTuple, Optional, TypedDict

import sentry_sdk


class LayoutPrimitive(str, enum.Enum):
    """The four canonical face-layout primitives."""

    FULLSCREEN = "fullscreen"
    SPLIT_H = "split_h"
    PIP_QUARTER_BL = "pip_quarter_bl"
    PIP_QUARTER_BR = "pip_quarter_br"


# Non-face state preserved alongside the four primitives. The avatar layer is
# omitted entirely (the timeline drops the V1 element) while audio still rides
# A1. Kept here so callers can route it without special-casing strings.
HIDDEN = "hidden"

# Safe-area gutter as a fraction of the canvas's shorter side. ~4% keeps the
# PIP face clear of TikTok's reserved UI zones on portrait canvases.
MARGIN_FRAC = 0.04


class Rect(NamedTuple):
    """An axis-aligned rectangle in canvas pixels (x, y from top-left)."""

    x: int
    y: int
    w: int
    h: int


class Geometry(TypedDict):
    """Result of ``compute_geometry``.

    ``face_rect`` is where the talking face goes (None only for ``hidden``).
    ``content_rect`` is where product / b-roll content goes — non-None only for
    ``split_h`` (the half NOT occupied by the face).
    """

    primitive: str
    face_rect: Optional[Rect]
    content_rect: Optional[Rect]
    visible: bool


# ── Legacy alias table (old vocabulary → canonical) ────────────────────────
#
# Every historical layout/face string maps onto one of the four primitives (or
# the preserved ``hidden`` state). Unknown values degrade to FULLSCREEN with a
# Sentry capture so bad upstream data is visible without breaking a render.
LEGACY_LAYOUT_ALIASES: dict[str, str] = {
    # already-canonical
    "fullscreen": LayoutPrimitive.FULLSCREEN.value,
    "split_h": LayoutPrimitive.SPLIT_H.value,
    "pip_quarter_bl": LayoutPrimitive.PIP_QUARTER_BL.value,
    # The talking head always rides bottom-LEFT so it never collides with the
    # bottom-right product card. Any bottom-right request is snapped left.
    "pip_quarter_br": LayoutPrimitive.PIP_QUARTER_BL.value,
    # legacy fullscreen
    "full": LayoutPrimitive.FULLSCREEN.value,
    "full_avatar": LayoutPrimitive.FULLSCREEN.value,
    "avatar_full": LayoutPrimitive.FULLSCREEN.value,
    # legacy half-splits
    "top_half": LayoutPrimitive.SPLIT_H.value,
    "bottom_half": LayoutPrimitive.SPLIT_H.value,
    "split_screen": LayoutPrimitive.SPLIT_H.value,
    # legacy corner PIPs → quarter PIP (default bottom-left)
    "pip_small": LayoutPrimitive.PIP_QUARTER_BL.value,
    "pip_medium": LayoutPrimitive.PIP_QUARTER_BL.value,
    "pip_small_bl": LayoutPrimitive.PIP_QUARTER_BL.value,
    "pip_small_br": LayoutPrimitive.PIP_QUARTER_BL.value,  # snapped left — see above
    # preserved no-face state
    "hidden": HIDDEN,
}

# Legacy values whose canonical SPLIT_H form puts the face on the BOTTOM half.
# Everything else that maps to SPLIT_H defaults to the face on top.
_SPLIT_FACE_BOTTOM_LEGACY = {"bottom_half"}


def coerce_to_primitive(value: object) -> str:
    """Map any legacy / arbitrary layout value to a canonical string.

    Returns one of the four ``LayoutPrimitive`` values or the preserved
    ``hidden`` state. Unknown / malformed input degrades to ``fullscreen`` with
    a Sentry capture (never raises).
    """
    if isinstance(value, LayoutPrimitive):
        # Route through the alias table too so PIP_QUARTER_BR (enum) is snapped
        # to bottom-left like its string form — the talking head always rides
        # left, clear of the bottom-right product card.
        return LEGACY_LAYOUT_ALIASES.get(value.value, value.value)
    if isinstance(value, str):
        key = value.strip().lower()
        mapped = LEGACY_LAYOUT_ALIASES.get(key)
        if mapped is not None:
            return mapped
        sentry_sdk.capture_exception(ValueError(f"unknown layout value {value!r}"))
        return LayoutPrimitive.FULLSCREEN.value
    if value is None:
        return LayoutPrimitive.FULLSCREEN.value
    sentry_sdk.capture_exception(ValueError(f"non-string layout value {value!r}"))
    return LayoutPrimitive.FULLSCREEN.value


def split_face_position_for_legacy(value: object) -> str:
    """Return the ``split_face_position`` ("top"|"bottom") implied by a legacy
    value. ``bottom_half`` puts the face on the bottom; everything else → top.
    """
    if isinstance(value, str) and value.strip().lower() in _SPLIT_FACE_BOTTOM_LEGACY:
        return "bottom"
    return "top"


def _margin_px(canvas_w: int, canvas_h: int) -> int:
    """Safe-area gutter in pixels: ~4% of the canvas's shorter side."""
    return int(round(min(canvas_w, canvas_h) * MARGIN_FRAC))


def compute_geometry(
    primitive: object,
    canvas_w: int,
    canvas_h: int,
    opts: Optional[dict] = None,
) -> Geometry:
    """Compute face/content rectangles for a layout primitive on a canvas.

    Args:
        primitive: a ``LayoutPrimitive``, a canonical string, or any legacy /
            arbitrary value (coerced via ``coerce_to_primitive``).
        canvas_w / canvas_h: canvas size in pixels.
        opts: optional knobs. Currently only ``split_face_position`` ("top" |
            "bottom", default "top") for SPLIT_H.

    Returns:
        A ``Geometry`` dict. ``face_rect`` is None only for ``hidden`` (which
        also sets ``visible=False``). ``content_rect`` is non-None only for
        ``split_h``.

    Geometry (parametric on W=canvas_w, H=canvas_h, m=margin≈4% of min(W,H)):
        fullscreen      face=(0,0,W,H)                         content=None
        split_h (top)   face=(0,0,W,H/2)   content=(0,H/2,W,H-H/2)
        split_h (bottom) face=(0,H/2,W,H-H/2) content=(0,0,W,H/2)
        pip_quarter_bl  face=(m, H-H/2-m+?, W/2-m*1.5, H/2-m)  bottom-left
        pip_quarter_br  same size, anchored bottom-right
    The PIP face spans ~W/2 × ~H/2 (minus margins) ≈ a quarter of the canvas
    area, anchored to the bottom corner with the safe gutter.
    """
    opts = opts or {}
    try:
        w = int(canvas_w)
        h = int(canvas_h)
        if w <= 0 or h <= 0:
            raise ValueError(f"non-positive canvas {canvas_w}x{canvas_h}")
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        w, h = 480, 848

    prim = coerce_to_primitive(primitive)

    if prim == HIDDEN:
        return {
            "primitive": HIDDEN,
            "face_rect": None,
            "content_rect": None,
            "visible": False,
        }

    if prim == LayoutPrimitive.FULLSCREEN.value:
        return {
            "primitive": prim,
            "face_rect": Rect(0, 0, w, h),
            "content_rect": None,
            "visible": True,
        }

    if prim == LayoutPrimitive.SPLIT_H.value:
        top_h = h // 2
        bottom_h = h - top_h  # absorbs the odd pixel so halves sum to H
        top = Rect(0, 0, w, top_h)
        bottom = Rect(0, top_h, w, bottom_h)
        face_pos = str(opts.get("split_face_position", "top")).strip().lower()
        if face_pos == "bottom":
            return {
                "primitive": prim,
                "face_rect": bottom,
                "content_rect": top,
                "visible": True,
            }
        return {
            "primitive": prim,
            "face_rect": top,
            "content_rect": bottom,
            "visible": True,
        }

    # pip_quarter_bl / pip_quarter_br: a ~quarter-area face anchored to a bottom
    # corner with a safe gutter. Width ≈ W/2 - 1.5m, height ≈ H/2 - m.
    m = _margin_px(w, h)
    pip_w = max(1, int(round(w / 2 - m * 1.5)))
    pip_h = max(1, int(round(h / 2 - m)))
    pip_y = h - pip_h - m
    if prim == LayoutPrimitive.PIP_QUARTER_BR.value:
        pip_x = w - pip_w - m
    else:  # PIP_QUARTER_BL
        pip_x = m

    pip_x = max(0, min(pip_x, w - pip_w))
    pip_y = max(0, min(pip_y, h - pip_h))

    return {
        "primitive": prim,
        "face_rect": Rect(pip_x, pip_y, pip_w, pip_h),
        "content_rect": None,
        "visible": True,
    }
