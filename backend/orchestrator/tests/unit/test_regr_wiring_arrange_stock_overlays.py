"""regr-wiring: auto_arrange emits visual overlays for stock_video + split_h.

PR #143 added overlay emission in the adapter `translate_timeline_to_overlays_v2`,
but that adapter is NOT on the finalize -> render path. The live path saves the
timeline tracks built by `auto_arrange_cast_timeline` and reads non-bonded
elements back via `tasks.cast_render.extract_overlay_elements` at render time.

These tests lock the surgical fix:

  * `build_stock_overlay_element` (the pure helper the arrange loop calls) emits
    a FULLSCREEN overlay for `stock_video` blocks and a CONTENT-HALF overlay for
    split_h blocks, in render-canvas (480x848) coords;
  * blocks with no stock media / unhandled layouts emit nothing (no exception);
  * the emitted elements survive the real `extract_overlay_elements` round-trip
    at 1:1 scale (compositionWidth/Height == 480x848).

Pure unit tests — no DB, no auth, no FFmpeg.
"""
from __future__ import annotations

import pytest

from layouts.primitives import LayoutPrimitive, compute_geometry
from routers.casts import _get_pip_layout, build_stock_overlay_element
from tasks.cast_render import extract_overlay_elements

CANVAS_W = 480
CANVAS_H = 848


class _Block:
    def __init__(
        self,
        *,
        block_id: str = "blk1",
        category: str = "avatar_speaking",
        pip_layout: str | None = None,
        stock_media_url: str | None = None,
        stock_media_kind: str | None = None,
    ):
        self.id = block_id
        self.category = category
        self.block_metadata = {"pip_layout": pip_layout} if pip_layout else {}
        self.stock_media_url = stock_media_url
        self.stock_media_kind = stock_media_kind


# ── Bug 1: _get_pip_layout reads pip_layout from the JSONB metadata bag ──────
# The blocks table has NO pip_layout column; the value lives in metadata
# (Python attribute `block_metadata`, underlying column `metadata`).

class _MetaBlock:
    """Minimal block stub: only carries whatever metadata we assign."""

    def __init__(self, block_metadata):
        self.id = "blk_meta"
        self.block_metadata = block_metadata


def test_get_pip_layout_missing_metadata_returns_none():
    assert _get_pip_layout(_MetaBlock(None)) is None


def test_get_pip_layout_metadata_without_pip_layout_returns_none():
    assert _get_pip_layout(_MetaBlock({"product_carousel": True})) is None


def test_get_pip_layout_empty_string_returns_none():
    assert _get_pip_layout(_MetaBlock({"pip_layout": ""})) is None


@pytest.mark.parametrize(
    "value",
    ["split_h", "fullscreen", "pip_quarter_bl", "hidden"],
)
def test_get_pip_layout_returns_metadata_value(value):
    assert _get_pip_layout(_MetaBlock({"pip_layout": value})) == value


def test_get_pip_layout_non_dict_metadata_returns_none():
    assert _get_pip_layout(_MetaBlock("not-a-dict")) is None


# ── helper: fullscreen stock_video ──────────────────────────────────────────

def test_stock_video_block_emits_fullscreen_overlay():
    block = _Block(
        category="stock_video",
        stock_media_url="https://x/clip.mp4",
        stock_media_kind="video",
    )
    el = build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H)
    assert el is not None
    assert el["type"] == "video"
    assert el["s"] == 0.0 and el["e"] == 3.0
    assert el["props"] == {
        "src": "https://x/clip.mp4",
        "x": 0,
        "y": 0,
        "width": CANVAS_W,
        "height": CANVAS_H,
    }
    assert el["metadata"]["kind"] == "stock_fullscreen"
    assert el["metadata"]["block_id"] == "blk1"
    # NOT bonded — extract_overlay_elements must surface it.
    assert "bonded" not in el["metadata"]


def test_stock_video_image_kind_emits_image_type():
    block = _Block(
        category="stock_video",
        stock_media_url="https://x/still.jpg",
        stock_media_kind="photo",
    )
    el = build_stock_overlay_element(block, 0.0, 2.0, CANVAS_W, CANVAS_H)
    assert el is not None
    assert el["type"] == "image"


# ── helper: split_h content half ────────────────────────────────────────────

def test_split_h_block_emits_content_half_overlay():
    block = _Block(
        category="pip_talking_head",
        pip_layout="split_h",
        stock_media_url="https://x/broll.mp4",
        stock_media_kind="video",
    )
    el = build_stock_overlay_element(block, 1.0, 4.0, CANVAS_W, CANVAS_H)
    assert el is not None
    assert el["metadata"]["kind"] == "stock_content_half"

    content = compute_geometry(LayoutPrimitive.SPLIT_H.value, CANVAS_W, CANVAS_H)[
        "content_rect"
    ]
    assert (
        el["props"]["x"],
        el["props"]["y"],
        el["props"]["width"],
        el["props"]["height"],
    ) == (content.x, content.y, content.w, content.h)
    # Content half is exactly half the canvas height (face owns the other half).
    assert el["props"]["height"] == CANVAS_H // 2
    assert el["props"]["height"] < CANVAS_H


# ── helper: no-emit cases ───────────────────────────────────────────────────

def test_fullscreen_layout_non_stock_video_emits_nothing():
    block = _Block(
        category="avatar_speaking",
        pip_layout="fullscreen",
        stock_media_url="https://x/clip.mp4",
        stock_media_kind="video",
    )
    assert build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H) is None


def test_empty_stock_media_url_emits_nothing():
    block = _Block(
        category="stock_video",
        stock_media_url="",
        stock_media_kind="video",
    )
    assert build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H) is None


def test_none_stock_media_url_emits_nothing():
    block = _Block(category="stock_video", stock_media_url=None)
    assert build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H) is None


def test_split_h_without_stock_emits_nothing():
    block = _Block(category="pip_talking_head", pip_layout="split_h", stock_media_url=None)
    assert build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H) is None


# ── round-trip through the real render-time extractor ───────────────────────

def _timeline_with(*elements: dict) -> dict:
    """A minimal saved-timeline shape: one bonded face element (skipped by the
    extractor) plus the stock track under test, with native-canvas composition
    dims so the extractor scales 1:1."""
    bonded = {
        "id": "v1_blk1",
        "type": "video",
        "s": 0.0,
        "e": 3.0,
        "props": {"src": "https://x/seg.mp4"},
        "metadata": {"block_id": "blk1", "bonded": True, "track_type": "video_face"},
    }
    return {
        "tracks": [
            {"id": "video", "type": "video", "elements": [bonded]},
            {"id": "stock", "type": "video", "elements": list(elements)},
        ],
        "version": 1,
        "compositionWidth": CANVAS_W,
        "compositionHeight": CANVAS_H,
    }


def test_fullscreen_overlay_round_trips_through_extractor():
    block = _Block(
        category="stock_video",
        stock_media_url="https://x/clip.mp4",
        stock_media_kind="video",
    )
    el = build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H)
    overlays = extract_overlay_elements(
        _timeline_with(el), render_width=CANVAS_W, render_height=CANVAS_H
    )
    # Exactly one visual overlay (the bonded face is skipped).
    vids = [o for o in overlays if o["type"] == "video"]
    assert len(vids) == 1
    ov = vids[0]
    assert ov["src"] == "https://x/clip.mp4"
    # 1:1 scaling — coords unchanged.
    assert (ov["x"], ov["y"], ov["width"], ov["height"]) == (0, 0, CANVAS_W, CANVAS_H)


def test_content_half_overlay_round_trips_through_extractor():
    block = _Block(
        category="pip_talking_head",
        pip_layout="split_h",
        stock_media_url="https://x/broll.mp4",
        stock_media_kind="video",
    )
    el = build_stock_overlay_element(block, 0.0, 3.0, CANVAS_W, CANVAS_H)
    overlays = extract_overlay_elements(
        _timeline_with(el), render_width=CANVAS_W, render_height=CANVAS_H
    )
    vids = [o for o in overlays if o["type"] == "video"]
    assert len(vids) == 1
    content = compute_geometry(LayoutPrimitive.SPLIT_H.value, CANVAS_W, CANVAS_H)[
        "content_rect"
    ]
    ov = vids[0]
    assert (ov["x"], ov["y"], ov["width"], ov["height"]) == (
        content.x,
        content.y,
        content.w,
        content.h,
    )
