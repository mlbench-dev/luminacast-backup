"""Composer reads template layout (face primitive + overlay slot).

Pure unit tests (no DB) for the placement surface AFTER the regression-5
canonicalization to four layout primitives (``fullscreen`` / ``split_h`` /
``pip_quarter_bl`` / ``pip_quarter_br``; plus the preserved ``hidden`` state):

  * ``services.timeline_builder.face_to_pip_layout`` — every template `face`
    value (canonical or legacy) maps to a primitive (or hidden / fullscreen).
  * ``services.timeline_builder.pip_geometry`` — fullscreen / split_h / quarter
    PIP geometry on the reference canvas.
  * ``services.timeline_builder.resolve_block_pip_layout`` — a block-level
    override beats the template face default (coerced to a primitive).
  * ``services.twick_compositor_adapter.resolve_overlay_placement`` — each
    anchor + width_frac resolves to the expected pixel geometry (within
    tolerance), and a missing template keeps the bottom-third default.
  * ``hidden`` face + no b-roll → the resolution/placement path does not raise.

These mirror the contract the renderer relies on in
``tasks/cast_render.py`` ``dispatch_and_upload`` and the FFmpeg PIP/overlay
compose stage; they intentionally avoid DB / ORM so CI's DB-free unit job runs
them unchanged.
"""
import pytest

from layouts.primitives import LayoutPrimitive
from services.timeline_builder import (
    face_to_pip_layout,
    pip_geometry,
    resolve_block_pip_layout,
    is_pip_layout,
)
from services.twick_compositor_adapter import resolve_overlay_placement


CANVAS_W = 1080
CANVAS_H = 1920


# ── face → primitive mapping ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "face, expected",
    [
        # canonical values map to themselves
        ("fullscreen", "fullscreen"),
        ("split_h", "split_h"),
        ("pip_quarter_bl", "pip_quarter_bl"),
        ("pip_quarter_br", "pip_quarter_br"),
        ("hidden", "hidden"),
        # legacy vocabulary resolves onto a primitive
        ("full", "fullscreen"),
        ("top_half", "split_h"),
        ("bottom_half", "split_h"),
        ("pip_small", "pip_quarter_bl"),
        ("pip_medium", "pip_quarter_bl"),
        ("pip_small_bl", "pip_quarter_bl"),
        ("pip_small_br", "pip_quarter_br"),
    ],
)
def test_face_to_pip_layout_maps_to_primitive(face, expected):
    assert face_to_pip_layout(face) == expected


def test_face_to_pip_layout_full_is_fullscreen():
    assert face_to_pip_layout("full") == LayoutPrimitive.FULLSCREEN.value


def test_face_to_pip_layout_is_case_insensitive_and_trims():
    assert face_to_pip_layout("  PIP_Small ") == "pip_quarter_bl"


@pytest.mark.parametrize("bad", ["nonsense", "", None, 123, {"x": 1}])
def test_face_to_pip_layout_unknown_degrades_to_fullscreen(bad):
    # Bad/unknown values must never raise — they degrade to fullscreen.
    assert face_to_pip_layout(bad) == LayoutPrimitive.FULLSCREEN.value


# ── split_h geometry ───────────────────────────────────────────────────────

def test_split_h_face_is_top_half_by_default():
    geo = pip_geometry("split_h", CANVAS_W, CANVAS_H)
    assert geo["visible"] is True
    # Face occupies the top half, full width, no rounded edge.
    assert geo["x"] == 0 and geo["y"] == 0
    assert geo["w"] == CANVAS_W
    assert geo["h"] == CANVAS_H // 2
    assert geo["border_radius"] == 0


def test_split_h_is_treated_as_pip_layout():
    # The renderer routes any non-fullscreen layout through the smaller bake.
    assert is_pip_layout("split_h") is True
    assert is_pip_layout("top_half") is True  # legacy alias
    assert is_pip_layout("full") is False
    assert is_pip_layout("fullscreen") is False


def test_full_face_geometry_is_fullscreen():
    geo = pip_geometry(face_to_pip_layout("full"), CANVAS_W, CANVAS_H)
    assert geo["w"] == CANVAS_W
    assert geo["h"] == CANVAS_H
    assert geo["x"] == 0 and geo["y"] == 0


def test_quarter_pip_carries_rounded_edge():
    geo = pip_geometry("pip_quarter_bl", CANVAS_W, CANVAS_H)
    assert geo["visible"] is True
    assert geo["border_radius"] > 0
    assert geo["bake_size"] > 0


def test_hidden_face_geometry_is_not_visible():
    geo = pip_geometry(face_to_pip_layout("hidden"), CANVAS_W, CANVAS_H)
    assert geo["visible"] is False


# ── block-level override wins over template default ────────────────────────

class _FakeBlock:
    def __init__(self, pip_layout=None):
        if pip_layout is None:
            self.block_metadata = {}
        else:
            self.block_metadata = {"pip_layout": pip_layout}


def test_block_override_wins_over_template_face():
    # Block set pip_small (legacy → pip_quarter_bl); template says hidden.
    block = _FakeBlock(pip_layout="pip_small")
    assert resolve_block_pip_layout(block, template_face="hidden") == "pip_quarter_bl"


def test_block_override_fullscreen_beats_template_hidden():
    block = _FakeBlock(pip_layout="fullscreen")
    assert resolve_block_pip_layout(block, template_face="hidden") == "fullscreen"


def test_template_face_used_when_block_has_no_override():
    block = _FakeBlock(pip_layout=None)
    assert resolve_block_pip_layout(block, template_face="hidden") == "hidden"
    assert resolve_block_pip_layout(block, template_face="top_half") == "split_h"


def test_no_block_override_and_no_template_is_fullscreen():
    block = _FakeBlock(pip_layout=None)
    assert resolve_block_pip_layout(block, template_face=None) == "fullscreen"


def test_blank_block_override_falls_through_to_template():
    # An empty-string pip_layout is not a real override.
    block = _FakeBlock(pip_layout="   ")
    assert resolve_block_pip_layout(block, template_face="pip_medium") == "pip_quarter_bl"


# ── overlay placement: template branch ─────────────────────────────────────

def _tpl(anchor, width_frac, margin):
    return {"config": {"overlay": {
        "anchor": anchor, "width_frac": width_frac, "margin": margin,
    }}}


def test_overlay_bottom_center_width_and_x():
    p = resolve_overlay_placement(_tpl("bottom_center", 0.28, 40), CANVAS_W, CANVAS_H)
    assert p["source"] == "template"
    assert p["anchor"] == "bottom_center"
    assert p["width"] == pytest.approx(0.28 * CANVAS_W, abs=1)
    # Centred horizontally.
    assert p["x"] == pytest.approx((CANVAS_W - p["width"]) / 2, abs=1)
    # margin 40 on the 1920 reference canvas → 40px up from the bottom edge.
    assert p["y"] == pytest.approx(CANVAS_H - 40, abs=1)


def test_overlay_bottom_left_hugs_left_margin():
    p = resolve_overlay_placement(_tpl("bottom_left", 0.3, 40), CANVAS_W, CANVAS_H)
    assert p["x"] == p["margin"]
    assert p["y"] == pytest.approx(CANVAS_H - 40, abs=1)


def test_overlay_bottom_right_hugs_right_margin():
    p = resolve_overlay_placement(_tpl("bottom_right", 0.3, 40), CANVAS_W, CANVAS_H)
    expected_x = CANVAS_W - p["width"] - p["margin"]
    assert p["x"] == pytest.approx(expected_x, abs=1)


def test_overlay_top_center_hugs_top_margin():
    p = resolve_overlay_placement(_tpl("top_center", 0.4, 40), CANVAS_W, CANVAS_H)
    assert p["anchor"] == "top_center"
    assert p["y"] == p["margin"]
    assert p["x"] == pytest.approx((CANVAS_W - p["width"]) / 2, abs=1)


@pytest.mark.parametrize("width_frac", [0.1, 0.28, 0.5, 0.9])
def test_overlay_width_tracks_width_frac(width_frac):
    p = resolve_overlay_placement(_tpl("bottom_center", width_frac, 40), CANVAS_W, CANVAS_H)
    assert p["width"] == pytest.approx(width_frac * CANVAS_W, abs=1)


def test_overlay_margin_scales_with_canvas_height():
    # On a half-height canvas, a reference margin of 80 scales to ~40px.
    p = resolve_overlay_placement(_tpl("bottom_center", 0.28, 80), CANVAS_W, 960)
    assert p["margin"] == pytest.approx(40, abs=1)


def test_overlay_unknown_anchor_degrades_to_bottom_center():
    p = resolve_overlay_placement(_tpl("diagonal_unicorn", 0.28, 40), CANVAS_W, CANVAS_H)
    assert p["anchor"] == "bottom_center"


# ── overlay placement: default (no template) branch ────────────────────────

def test_overlay_no_template_uses_bottom_third_default():
    p = resolve_overlay_placement(None, CANVAS_W, CANVAS_H)
    assert p["source"] == "default"
    assert p["anchor"] == "bottom_center"
    # Bottom-third (~0.78 of canvas height).
    assert p["y"] == pytest.approx(CANVAS_H * 0.78, abs=2)


def test_overlay_template_without_overlay_config_falls_back_to_default():
    p = resolve_overlay_placement({"config": {"face": "fullscreen"}}, CANVAS_W, CANVAS_H)
    assert p["source"] == "default"


def test_overlay_accepts_orm_like_object_with_config_attr():
    class _Tpl:
        config = {"overlay": {"anchor": "bottom_left", "width_frac": 0.2, "margin": 40}}

    p = resolve_overlay_placement(_Tpl(), CANVAS_W, CANVAS_H)
    assert p["source"] == "template"
    assert p["anchor"] == "bottom_left"


# ── hidden + no b-roll: fallback path must not raise ───────────────────────

def test_hidden_face_resolution_does_not_raise_without_broll():
    # `hidden` means no avatar PIP; the picture comes from b-roll/scene. When
    # a block has no override and the template face is hidden, resolution must
    # produce `hidden` cleanly — never raise — so the renderer can take the
    # audio-only path without a face layer even if no b-roll is attached.
    block = _FakeBlock(pip_layout=None)
    layout = resolve_block_pip_layout(block, template_face="hidden")
    assert layout == "hidden"
    geo = pip_geometry(layout, CANVAS_W, CANVAS_H)
    assert geo["visible"] is False
    # Overlay placement still resolves (captions/CTA can still be placed even
    # when the face is hidden) — no template overlay attached → default.
    overlay = resolve_overlay_placement(None, CANVAS_W, CANVAS_H)
    assert overlay["source"] == "default"


def test_hidden_with_template_overlay_still_resolves():
    tpl = {"config": {"face": "hidden",
                      "overlay": {"anchor": "bottom_center", "width_frac": 0.28, "margin": 40}}}
    block = _FakeBlock(pip_layout=None)
    assert resolve_block_pip_layout(block, template_face=tpl["config"]["face"]) == "hidden"
    overlay = resolve_overlay_placement(tpl, CANVAS_W, CANVAS_H)
    assert overlay["source"] == "template"
    assert overlay["anchor"] == "bottom_center"
