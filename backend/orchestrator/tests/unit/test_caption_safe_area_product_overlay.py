"""Bug A (Round 7) — caption horizontal safe-area must be ABSOLUTE.

The user reported captions clipping at the LEFT edge on product-overlay
frames while talking-head frames were fine. The root cause: the usable
caption width was computed relative to whatever block layer sat on top, so
the implied left/right margin drifted between frame types.

The fix makes the safe area a constant absolute pixel margin (32px each
side) that depends ONLY on the canvas width — never on the block layer,
product overlay, or anything composited behind the caption. These tests
prove the caption x position and the usable width are byte-for-byte
identical whether the same caption is rendered over a talking-head block or
a product-overlay block.
"""
import os
import re

from services import cast_ffmpeg_composer as comp

CANVAS_W = 480
CANVAS_H = 854
RENDER_ID = "render-bugA"

CAPTION_TEXT = "Wait, your phone's dead AGAIN? I found the fix right now"


def _caption_element(eid: str) -> dict:
    return {
        "id": eid,
        "type": "caption",
        "s": 0.0,
        "e": 3.0,
        "props": {
            "text": CAPTION_TEXT,
            "preset": "hormozi_bold",
            "maxLines": 2,
        },
    }


def _timeline_with_top_layer(top_el: dict) -> dict:
    """A timeline carrying one caption plus one non-caption top layer."""
    return {
        "tracks": [
            {"id": "elem", "type": "element", "elements": [top_el]},
            {"id": "cap", "type": "caption", "elements": [_caption_element("c1")]},
        ]
    }


def _product_overlay_element() -> dict:
    # A product-overlay block: a full-frame element layered above the video.
    return {
        "id": "prod1",
        "type": "image",
        "s": 0.0,
        "e": 3.0,
        "props": {"src": "product.png", "kind": "product_overlay"},
        "metadata": {"kind": "product_overlay"},
    }


def _talking_head_element() -> dict:
    # A talking-head block: a bonded baked video segment.
    return {
        "id": "th1",
        "type": "video",
        "s": 0.0,
        "e": 3.0,
        "props": {"src": "head.mp4"},
        "metadata": {"kind": "talking_head"},
    }


def _drawtext_x_exprs(filter_complex: str) -> list[str]:
    return re.findall(r"drawtext=[^;]*?(x=\(w-text_w\)/2[^:]*)", filter_complex)


def _drawtext_filters(filter_complex: str) -> list[str]:
    """Every drawtext caption sub-filter, with the volatile output label and
    enable window stripped so we compare only text + geometry + style."""
    out = []
    for seg in filter_complex.split(";\n"):
        if "drawtext=" not in seg:
            continue
        seg = re.sub(r"\[[^\]]+\]", "", seg)  # drop [in]/[out] labels
        seg = re.sub(r"enable='[^']*'", "", seg)  # drop timing window
        out.append(seg.strip())
    return out


# ── Pure safe-area math: depends only on canvas_width ──────────────────────


def test_safe_inset_is_absolute_and_canvas_independent():
    # Same 32px margin on every canvas size — the heart of the Bug A fix.
    assert comp._caption_safe_inset(480) == 32
    assert comp._caption_safe_inset(720) == 32
    assert comp._caption_safe_inset(1080) == 32


def test_safe_width_is_canvas_minus_two_absolute_insets():
    assert comp._caption_safe_width(480) == 480 - 64
    assert comp._caption_safe_width(1080) == 1080 - 64


def test_safe_inset_env_override_still_absolute(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SAFE_AREA_INSET_PX", "48")
    assert comp._caption_safe_inset(480) == 48
    assert comp._caption_safe_inset(1080) == 48
    assert comp._caption_safe_width(480) == 480 - 96


def test_safe_inset_never_exceeds_half_a_tiny_canvas(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SAFE_AREA_INSET_PX", "9999")
    # Clamp so two insets can't consume the whole frame.
    assert comp._caption_safe_inset(100) == (100 - 1) // 2
    assert comp._caption_safe_width(100) >= 1


# ── Filtergraph-level: identical x and width across block types ─────────────


def test_caption_x_and_width_identical_product_overlay_vs_talking_head(
    monkeypatch,
):
    # Force the drawtext path (not SSR) so we can inspect x/width directly.
    monkeypatch.setenv("CAPTIONS_SSR_ENABLED", "0")
    monkeypatch.delenv("CAPTIONS_SAFE_AREA_INSET_PX", raising=False)

    th_timeline = _timeline_with_top_layer(_talking_head_element())
    po_timeline = _timeline_with_top_layer(_product_overlay_element())

    th_plan = comp.translate_timeline_to_ffmpeg(
        th_timeline, {}, RENDER_ID, CANVAS_W, CANVAS_H
    )
    po_plan = comp.translate_timeline_to_ffmpeg(
        po_timeline, {}, RENDER_ID, CANVAS_W, CANVAS_H
    )

    th_x = _drawtext_x_exprs(th_plan.filter_complex)
    po_x = _drawtext_x_exprs(po_plan.filter_complex)

    # A caption was actually emitted on both.
    assert th_x, "expected a drawtext caption on the talking-head timeline"
    assert po_x, "expected a drawtext caption on the product-overlay timeline"

    # Centered x is identical and never offset by the top layer.
    assert th_x == po_x
    assert all(x == "x=(w-text_w)/2" for x in th_x)

    # The entire caption drawtext chain (text wrap + geometry + style) is
    # byte-identical between the two block types — proving the wrap width,
    # and therefore the rendered left/right margin, does not depend on what
    # is composited behind the caption. Before the Bug A fix the width drifted
    # with the top layer, which is what clipped the product-overlay frames.
    th_caps = _drawtext_filters(th_plan.filter_complex)
    po_caps = _drawtext_filters(po_plan.filter_complex)
    assert th_caps == po_caps
    assert th_caps, "expected at least one drawtext caption filter"
