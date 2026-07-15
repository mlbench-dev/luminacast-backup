"""Regression-5 — four canonical layout primitives.

Pure unit tests (no DB) for ``layouts.primitives``:

  * ``compute_geometry`` returns in-canvas, non-negative rects; the two
    split_h halves tile the canvas exactly.
  * ``coerce_to_primitive`` maps every legacy value onto one of the four
    primitives (or the preserved ``hidden`` state); unknown → fullscreen.
  * ``apply_pip_to_v1_element`` places a PIP_QUARTER_BR face in the
    bottom-right quadrant.
  * The 10 shipped layout-template presets carry a canonical ``face`` value
    (post-migration snapshot).
"""
import pytest

from layouts.primitives import (
    LEGACY_LAYOUT_ALIASES,
    HIDDEN,
    LayoutPrimitive,
    coerce_to_primitive,
    compute_geometry,
    split_face_position_for_legacy,
)
from services.timeline_builder import apply_pip_to_v1_element
from scripts.seed_layout_templates import PRESETS


# Portrait canvas the renderer actually uses + the reference design canvas.
CANVASES = [(480, 848), (1080, 1920), (720, 1280)]

_CANONICAL = {p.value for p in LayoutPrimitive}


# ── compute_geometry: rects stay inside the canvas ─────────────────────────

def _assert_in_canvas(rect, w, h):
    assert rect is not None
    assert rect.x >= 0 and rect.y >= 0
    assert rect.w > 0 and rect.h > 0
    assert rect.x + rect.w <= w
    assert rect.y + rect.h <= h


@pytest.mark.parametrize("w,h", CANVASES)
@pytest.mark.parametrize("prim", list(_CANONICAL))
def test_face_rect_within_canvas_no_negative(prim, w, h):
    geo = compute_geometry(prim, w, h)
    _assert_in_canvas(geo["face_rect"], w, h)


@pytest.mark.parametrize("w,h", CANVASES)
def test_fullscreen_fills_canvas(w, h):
    geo = compute_geometry(LayoutPrimitive.FULLSCREEN, w, h)
    face = geo["face_rect"]
    assert (face.x, face.y, face.w, face.h) == (0, 0, w, h)
    assert geo["content_rect"] is None


@pytest.mark.parametrize("w,h", CANVASES)
def test_split_halves_tile_the_canvas(w, h):
    geo = compute_geometry(LayoutPrimitive.SPLIT_H, w, h)
    face, content = geo["face_rect"], geo["content_rect"]
    _assert_in_canvas(face, w, h)
    _assert_in_canvas(content, w, h)
    # Both halves are full width.
    assert face.w == content.w == w
    # The two halves sum to the full canvas height with no gap / overlap.
    assert face.h + content.h == h
    # Default face on top.
    assert face.y == 0
    assert content.y == face.h


@pytest.mark.parametrize("w,h", CANVASES)
def test_split_face_bottom_option(w, h):
    geo = compute_geometry(
        LayoutPrimitive.SPLIT_H, w, h, {"split_face_position": "bottom"}
    )
    face, content = geo["face_rect"], geo["content_rect"]
    assert content.y == 0
    assert face.y == content.h
    assert face.h + content.h == h


@pytest.mark.parametrize("w,h", CANVASES)
def test_quarter_pips_are_roughly_quarter_area_and_bottom_anchored(w, h):
    for prim in (LayoutPrimitive.PIP_QUARTER_BL, LayoutPrimitive.PIP_QUARTER_BR):
        geo = compute_geometry(prim, w, h)
        face = geo["face_rect"]
        _assert_in_canvas(face, w, h)
        # ~1/4 of canvas area (allow a generous band for the margins).
        area_frac = (face.w * face.h) / (w * h)
        assert 0.12 <= area_frac <= 0.30
        # Anchored to the bottom.
        assert face.y + face.h <= h
        assert face.y > h * 0.4
        assert geo["content_rect"] is None


@pytest.mark.parametrize("w,h", CANVASES)
def test_quarter_bl_hugs_left_and_br_hugs_right(w, h):
    bl = compute_geometry(LayoutPrimitive.PIP_QUARTER_BL, w, h)["face_rect"]
    br = compute_geometry(LayoutPrimitive.PIP_QUARTER_BR, w, h)["face_rect"]
    # BL near the left edge; BR's right edge near the right canvas edge.
    margin = int(round(min(w, h) * 0.04))
    assert bl.x == margin
    assert br.x + br.w == pytest.approx(w - margin, abs=2)


def test_hidden_has_no_face_rect():
    geo = compute_geometry(HIDDEN, 1080, 1920)
    assert geo["face_rect"] is None
    assert geo["content_rect"] is None
    assert geo["visible"] is False


def test_bad_canvas_does_not_raise():
    geo = compute_geometry(LayoutPrimitive.FULLSCREEN, 0, -5)
    # Degrades to a safe portrait fallback rather than raising.
    assert geo["face_rect"] is not None
    assert geo["face_rect"].w > 0 and geo["face_rect"].h > 0


# ── coerce_to_primitive: every legacy value maps to a primitive ────────────

@pytest.mark.parametrize("legacy", sorted(LEGACY_LAYOUT_ALIASES))
def test_every_alias_maps_to_primitive_or_hidden(legacy):
    result = coerce_to_primitive(legacy)
    assert result in _CANONICAL or result == HIDDEN


@pytest.mark.parametrize(
    "legacy, expected",
    [
        ("full", "fullscreen"),
        ("top_half", "split_h"),
        ("bottom_half", "split_h"),
        ("pip_small", "pip_quarter_bl"),
        ("pip_medium", "pip_quarter_bl"),
        ("pip_small_bl", "pip_quarter_bl"),
        ("pip_small_br", "pip_quarter_br"),
        ("hidden", "hidden"),
    ],
)
def test_specific_legacy_mappings(legacy, expected):
    assert coerce_to_primitive(legacy) == expected


@pytest.mark.parametrize("bad", ["nonsense", "", None, 123, {"x": 1}, []])
def test_unknown_degrades_to_fullscreen(bad):
    assert coerce_to_primitive(bad) == LayoutPrimitive.FULLSCREEN.value


def test_coerce_is_case_insensitive_and_trims():
    assert coerce_to_primitive("  TOP_HALF ") == "split_h"


def test_bottom_half_sets_split_face_bottom():
    assert split_face_position_for_legacy("bottom_half") == "bottom"
    assert split_face_position_for_legacy("top_half") == "top"
    assert split_face_position_for_legacy("split_h") == "top"


# ── apply_pip_to_v1_element: PIP_QUARTER_BR lands bottom-right ──────────────

def test_apply_pip_quarter_br_places_face_bottom_right():
    w, h = 480, 848
    el = {"id": "v1_blk_x", "type": "video", "props": {}, "metadata": {}}
    out = apply_pip_to_v1_element(el, "pip_quarter_br", w, h)
    assert out is not None
    props = out["props"]
    margin = int(round(min(w, h) * 0.04))
    # Right edge of the face is flush with the canvas minus the safe margin.
    assert props["x"] + props["width"] == pytest.approx(w - margin, abs=2)
    # Bottom edge sits within a margin of the canvas bottom.
    assert props["y"] + props["height"] == pytest.approx(h - margin, abs=2)
    # In the right half of the canvas.
    assert props["x"] >= w / 2 - margin * 2


def test_apply_pip_quarter_bl_places_face_bottom_left():
    w, h = 480, 848
    el = {"id": "v1_blk_y", "type": "video", "props": {}, "metadata": {}}
    out = apply_pip_to_v1_element(el, "pip_quarter_bl", w, h)
    assert out is not None
    props = out["props"]
    margin = int(round(min(w, h) * 0.04))
    assert props["x"] == margin
    assert props["y"] + props["height"] == pytest.approx(h - margin, abs=2)


def test_apply_pip_hidden_returns_none():
    el = {"id": "v1_blk_z", "type": "video", "props": {}, "metadata": {}}
    assert apply_pip_to_v1_element(el, "hidden", 480, 848) is None


# ── 10 layout-template presets carry a canonical face (post-migration) ──────

def test_all_ten_presets_use_canonical_face_values():
    allowed = _CANONICAL | {HIDDEN}
    faces = {slug: config["face"] for slug, _, config in PRESETS}
    assert len(faces) == 10
    bad = {slug: f for slug, f in faces.items() if f not in allowed}
    assert not bad, f"presets with non-canonical face: {bad}"


def test_preset_face_snapshot():
    # Frozen snapshot of the post-migration face vocabulary for the 10 presets.
    expected = {
        "talking_head": "fullscreen",
        "voiceover_explainer": "hidden",
        "product_spotlight": "split_h",
        "split_demo": "split_h",
        "creative_motion": "hidden",
        "educational_lecture": "pip_quarter_bl",
        "story_vlog": "fullscreen",
        "ugc_review": "fullscreen",
        "fashion_lookbook": "fullscreen",
        "live_selling": "fullscreen",
    }
    actual = {slug: config["face"] for slug, _, config in PRESETS}
    assert actual == expected
