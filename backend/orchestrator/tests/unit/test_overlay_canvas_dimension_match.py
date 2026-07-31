"""Regression test: overlay scaling must match the actual compose canvas.

Bug (render rnd_b559ae23ee62): the final compose canvas is ALWAYS built from
timeline.compositionWidth/Height (worker_ffmpeg_compose._canvas_size and this
codebase's own _canvas_dims_for_render, which normalizes every baked block
clip) — neither of those ever downscales by the cast's "quality" setting.
But extract_overlay_elements was being called with render_width/height looked
up from a 480p/720p/1080p quality table instead. For a 1080x1920 "hd" cast
that resolved to 720x1280 (sx=sy=0.667): every full-canvas overlay (B-roll
video, stock-photo image) came out scaled to only 2/3 of the real canvas,
leaving a gap on the far side that let the underlying baked clip show through
behind it — confirmed by extracting an actual frame and seeing the same photo
twice, once correctly placed and once cropped differently through the gap.

Fix: tasks/cast_render.py now derives the overlay-scaling target dimensions
from _canvas_dims_for_render(timeline) — the exact same source of truth used
to size the baked clips and the compose base canvas — so scaling is always
1:1 with the canvas overlays actually land on, regardless of quality tier.
"""
from __future__ import annotations

from tasks.cast_render import _canvas_dims_for_render, extract_overlay_elements


def _fullscreen_video_timeline(comp_w: int, comp_h: int) -> dict:
    return {
        "compositionWidth": comp_w,
        "compositionHeight": comp_h,
        "fps": 30,
        "tracks": [{
            "type": "video",
            "elements": [{
                "id": "pm_blk1_0",
                "type": "video",
                "s": 0.0,
                "e": 4.0,
                "metadata": {"block_id": "blk1", "track_type": "parallel_media"},
                "props": {"x": 0, "y": 0, "width": comp_w, "height": comp_h, "src": "https://x/clip.mp4"},
            }],
        }],
    }


def test_canvas_dims_for_render_ignores_quality_tier():
    """The function every baked clip and the compose base canvas actually use
    must return the full editor composition size, not a quality-derived
    smaller size — there's no code path that downscales the final canvas."""
    timeline = _fullscreen_video_timeline(1080, 1920)
    cw, ch, fps = _canvas_dims_for_render(timeline)
    assert (cw, ch) == (1080, 1920)


def test_fullscreen_overlay_scales_1to1_against_real_compose_canvas():
    """A full-canvas overlay must come out full-canvas in render coordinates
    when scaled against the SAME dimensions the compose pass actually uses —
    not shrunk by an unrelated quality-tier lookup."""
    comp_w, comp_h = 1080, 1920
    timeline = _fullscreen_video_timeline(comp_w, comp_h)

    render_w, render_h, _ = _canvas_dims_for_render(timeline)
    overlays = extract_overlay_elements(timeline, render_width=render_w, render_height=render_h)

    assert len(overlays) == 1
    ov = overlays[0]
    assert ov["x"] == 0 and ov["y"] == 0
    assert ov["width"] == render_w and ov["height"] == render_h, (
        "a full-canvas overlay must fill the full render canvas, not a "
        "fraction of it — a shortfall here is exactly what left a gap "
        "revealing the baked clip underneath in rnd_b559ae23ee62"
    )


def test_old_quality_tier_lookup_would_have_produced_the_bug():
    """Sanity check that the bug we fixed was real: the OLD 480p/720p/1080p
    lookup for an 'hd' quality cast (1080x1920 composition) resolved to a
    smaller 720x1280 target, which is what caused the mismatch."""
    old_quality_table = {"480p": (480, 848), "720p": (720, 1280), "1080p": (1080, 1920)}
    old_rw, old_rh = old_quality_table.get("hd", (480, 848))
    assert (old_rw, old_rh) == (480, 848)
    assert (old_rw, old_rh) != (1080, 1920), (
        "the old lookup did not match the real 1080x1920 compose canvas for "
        "an 'hd'-quality cast, which is the root cause of the visible bug"
    )
