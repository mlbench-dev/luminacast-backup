"""regr-wiring: visual overlays for split_h content halves + stock_video.

These lock the contract that ``translate_timeline_to_overlays_v2`` (the live
adapter consumed by ``tasks.generate_cast._run_twick_compositor``) emits the
visual overlays that fill an otherwise-black canvas:

  * a SPLIT_H block carrying a stock-media URL gets a content-half overlay
    positioned with the EXACT ``compute_geometry`` content_rect;
  * a ``stock_video`` category block (no avatar bake) gets a FULLSCREEN
    overlay spanning the whole canvas;
  * a SPLIT_H block with NO stock_media_url emits NO visual overlay and
    raises no exception.

Pure unit tests — no DB, no network, no FFmpeg.
"""
from __future__ import annotations

import pytest

from layouts.primitives import LayoutPrimitive, compute_geometry
from services.twick_compositor_adapter import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    translate_timeline_to_overlays_v2,
)


class _Block:
    """Minimal block stub matching the attributes the adapter reads."""

    def __init__(
        self,
        *,
        block_id: str,
        category: str = "product",
        pip_layout: str | None = None,
        stock_media_url: str | None = None,
        stock_media_kind: str | None = None,
        product_overlay_url: str | None = None,
    ):
        self.id = block_id
        self.category = category
        self.block_metadata = {"pip_layout": pip_layout} if pip_layout else {}
        self.stock_media_url = stock_media_url
        self.stock_media_kind = stock_media_kind
        self.product_overlay_url = product_overlay_url


def _regions(*block_ids: str) -> list[dict]:
    return [
        {"index": i, "block_id": bid, "start_s": float(i), "end_s": float(i + 1)}
        for i, bid in enumerate(block_ids)
    ]


def _empty_timeline() -> dict:
    return {"tracks": [], "version": 1}


def test_split_h_block_with_stock_emits_content_half_overlay():
    block = _Block(
        block_id="blk1",
        category="product",
        pip_layout="split_h",
        stock_media_url="https://example.com/clip.mp4",
        stock_media_kind="video",
    )
    out = translate_timeline_to_overlays_v2(
        _empty_timeline(), _regions("blk1"), [block]
    )
    overlays = out["product_overlays"].get(0, [])
    split = [o for o in overlays if o.get("source") == "split_h_content"]
    assert len(split) == 1
    ov = split[0]
    assert ov["src"] == "https://example.com/clip.mp4"
    assert ov["kind"] == "video"

    geo = compute_geometry(
        LayoutPrimitive.SPLIT_H.value,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
        {"split_face_position": "top"},
    )
    content = geo["content_rect"]
    assert (ov["x"], ov["y"], ov["width"], ov["height"]) == (
        content.x,
        content.y,
        content.w,
        content.h,
    )
    # The content half must NOT cover the whole canvas (face half is elsewhere).
    assert ov["height"] < CANVAS_HEIGHT


def test_stock_video_block_emits_fullscreen_overlay():
    block = _Block(
        block_id="blk1",
        category="stock_video",
        stock_media_url="https://example.com/full.mp4",
        stock_media_kind="video",
    )
    out = translate_timeline_to_overlays_v2(
        _empty_timeline(), _regions("blk1"), [block]
    )
    overlays = out["product_overlays"].get(0, [])
    fs = [o for o in overlays if o.get("source") == "stock_video_fullscreen"]
    assert len(fs) == 1
    ov = fs[0]
    assert ov["src"] == "https://example.com/full.mp4"
    assert ov["kind"] == "video"
    # Fullscreen: origin at (0,0) spanning the entire canvas.
    assert (ov["x"], ov["y"], ov["width"], ov["height"]) == (
        0,
        0,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
    )


def test_stock_video_block_image_kind_is_image():
    block = _Block(
        block_id="blk1",
        category="stock_video",
        stock_media_url="https://example.com/still.jpg",
        stock_media_kind="image",
    )
    out = translate_timeline_to_overlays_v2(
        _empty_timeline(), _regions("blk1"), [block]
    )
    fs = [
        o
        for o in out["product_overlays"].get(0, [])
        if o.get("source") == "stock_video_fullscreen"
    ]
    assert len(fs) == 1
    assert fs[0]["kind"] == "image"


def test_split_h_block_without_stock_emits_no_visual_overlay():
    block = _Block(
        block_id="blk1",
        category="product",
        pip_layout="split_h",
        stock_media_url=None,
    )
    # Must not raise, and must not emit a split_h / stock overlay.
    out = translate_timeline_to_overlays_v2(
        _empty_timeline(), _regions("blk1"), [block]
    )
    overlays = out["product_overlays"].get(0, [])
    assert not [
        o
        for o in overlays
        if o.get("source") in ("split_h_content", "stock_video_fullscreen")
    ]


def test_stock_video_block_without_url_emits_no_overlay():
    block = _Block(
        block_id="blk1",
        category="stock_video",
        stock_media_url=None,
    )
    out = translate_timeline_to_overlays_v2(
        _empty_timeline(), _regions("blk1"), [block]
    )
    overlays = out["product_overlays"].get(0, [])
    assert not [
        o for o in overlays if o.get("source") == "stock_video_fullscreen"
    ]


def test_pip_talking_head_split_h_uses_stock_media_url():
    block = _Block(
        block_id="blk1",
        category="pip_talking_head",
        pip_layout="split_h",
        stock_media_url="https://example.com/broll.mp4",
        stock_media_kind="video",
    )
    out = translate_timeline_to_overlays_v2(
        _empty_timeline(), _regions("blk1"), [block]
    )
    split = [
        o
        for o in out["product_overlays"].get(0, [])
        if o.get("source") == "split_h_content"
    ]
    assert len(split) == 1
    assert split[0]["src"] == "https://example.com/broll.mp4"
