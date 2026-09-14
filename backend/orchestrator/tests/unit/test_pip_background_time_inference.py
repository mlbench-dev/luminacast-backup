"""Client report: "I removed the image in the talking head and replaced it
with a video — in the rendered video the video was running on the talking
head so it's not visible." Confirmed root cause via DB inspection: the
block's `parallel_media` in the database still showed the OLD photo — the
user had added the replacement video directly from the Arrange editor's own
media panel (click a tile in "Uploaded" / "Stock" / "Generated" to drop it
onto the timeline), which is a completely separate code path from the
Script tab's b-roll picker and never writes `block_id` onto the item it
creates (LuminacastMediaPanel.tsx's handleAddToTimeline / the vendored
editor's own drop handlers build a bare VideoItem/ImageItem with no
`metadata` at all).

Consequence: cast_render.py's PIP-background resolver only ever matched an
overlay element to a PIP block via an exact `metadata.block_id` tag — an
untagged clip was invisible to it, so it fell through to the generic
overlay pass instead, which paints unconditionally over the ALREADY-
composited PIP corner (avatar included) — the talking head disappears
under it in the final render, regardless of how the client-side (and
backend-blind) editor preview happened to layer it.

Fix: when a PIP block has no block_id-tagged background, infer one by TIME
— a full-frame, untagged video/image overlay whose window substantially
(>=60%) overlaps that block's own active span is treated as its
background. An explicit block_id match always wins over the inferred one;
among untagged candidates the one with the largest overlap wins; a small
(non-full-frame) or only-briefly-overlapping untagged item is never
mistaken for it.

Pure-function test — mirrors the exact logic added to cast_render.py's PIP
resolution block (verified line-for-line against the real source), no DB
or network needed.
"""
from __future__ import annotations

from pathlib import Path

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_CAST_RENDER = _ORCH_ROOT / "tasks" / "cast_render.py"

CW, CH = 1080, 1920


def _is_pip_layout(pl):
    return pl in ("pip_quarter_bl", "pip_quarter_br", "pip_small", "pip_medium")


def _resolve_bg_by_block(overlay_elements, compose_video_tracks, cw_for_overlays, ch_for_overlays):
    """Faithful re-implementation of the PIP-background resolution block in
    cast_render.py (block_id match, then time-overlap inference) — kept
    import-free so it runs without the app's dependency stack."""

    def _is_fullframe(_ov):
        _ow = _ov.get("width") or cw_for_overlays
        _oh = _ov.get("height") or ch_for_overlays
        _ox = _ov.get("x") or 0
        _oy = _ov.get("y") or 0
        return (
            _ow >= cw_for_overlays * 0.95
            and _oh >= ch_for_overlays * 0.95
            and _ox <= cw_for_overlays * 0.05
            and _oy <= ch_for_overlays * 0.05
        )

    bg_by_block: dict = {}
    for ov in overlay_elements:
        if ov.get("type") not in ("video", "image"):
            continue
        bid = ov.get("block_id")
        if not bid or bid in bg_by_block:
            continue
        if _is_fullframe(ov):
            bg_by_block[bid] = ov

    pip_spans: dict = {}
    for vt in compose_video_tracks:
        bid = vt.get("block_id")
        if bid and _is_pip_layout(vt.get("pip_layout") or "fullscreen"):
            pip_spans[bid] = (float(vt.get("s") or 0), float(vt.get("e") or 0))

    untagged_fullframe = [
        ov for ov in overlay_elements
        if ov.get("type") in ("video", "image")
        and not ov.get("block_id")
        and _is_fullframe(ov)
    ]

    for bid, (bs, be) in pip_spans.items():
        if bid in bg_by_block or be <= bs:
            continue
        best, best_overlap = None, 0.0
        for ov in untagged_fullframe:
            try:
                os_ = float(ov.get("start_s") or 0)
                oe_ = float(ov.get("end_s") or 0)
            except (TypeError, ValueError):
                continue
            overlap = min(be, oe_) - max(bs, os_)
            if overlap >= 0.6 * (be - bs) and overlap > best_overlap:
                best, best_overlap = ov, overlap
        if best is not None:
            bg_by_block[bid] = best

    return bg_by_block


_PIP_TRACK = [{"block_id": "blk_pip", "pip_layout": "pip_quarter_bl", "s": 10.0, "e": 16.0}]


def test_untagged_clip_with_full_overlap_is_inferred_as_the_background():
    untagged = {
        "type": "video", "width": CW, "height": CH, "x": 0, "y": 0,
        "start_s": 10.0, "end_s": 16.0, "src": "https://x/new.mp4",
    }
    result = _resolve_bg_by_block([untagged], _PIP_TRACK, CW, CH)
    assert result.get("blk_pip") is untagged


def test_explicit_block_id_tag_always_wins_over_the_inference():
    tagged = {
        "type": "video", "block_id": "blk_pip", "width": CW, "height": CH, "x": 0, "y": 0,
        "start_s": 10.0, "end_s": 16.0, "src": "https://x/tagged.mp4",
    }
    distractor = {
        "type": "video", "width": CW, "height": CH, "x": 0, "y": 0,
        "start_s": 10.0, "end_s": 16.0, "src": "https://x/distractor.mp4",
    }
    result = _resolve_bg_by_block([tagged, distractor], _PIP_TRACK, CW, CH)
    assert result.get("blk_pip") is tagged


def test_brief_incidental_overlap_is_not_mistaken_for_the_background():
    brief = {
        "type": "video", "width": CW, "height": CH, "x": 0, "y": 0,
        "start_s": 15.5, "end_s": 17.0, "src": "https://x/unrelated.mp4",
    }
    result = _resolve_bg_by_block([brief], _PIP_TRACK, CW, CH)
    assert "blk_pip" not in result


def test_largest_overlap_wins_among_untagged_candidates():
    partial = {
        "type": "video", "width": CW, "height": CH, "x": 0, "y": 0,
        "start_s": 10.0, "end_s": 13.0, "src": "https://x/a.mp4",
    }
    full = {
        "type": "video", "width": CW, "height": CH, "x": 0, "y": 0,
        "start_s": 10.0, "end_s": 16.0, "src": "https://x/b.mp4",
    }
    result = _resolve_bg_by_block([partial, full], _PIP_TRACK, CW, CH)
    assert result.get("blk_pip") is full


def test_small_untagged_item_is_never_mistaken_for_a_pip_background():
    small = {
        "type": "video", "width": 300, "height": 300, "x": 500, "y": 500,
        "start_s": 10.0, "end_s": 16.0, "src": "https://x/small.mp4",
    }
    result = _resolve_bg_by_block([small], _PIP_TRACK, CW, CH)
    assert "blk_pip" not in result


def test_cast_render_source_contains_the_time_overlap_fallback():
    src = _CAST_RENDER.read_text(encoding="utf-8")
    start = src.find("_bg_by_block: dict[str, dict] = {}")
    assert start != -1
    end = src.find("\n        for _vt in compose_video_tracks:", start)
    assert end != -1
    body = src[start:end]
    assert "_untagged_fullframe" in body
    assert "0.6 * (_be - _bs)" in body
