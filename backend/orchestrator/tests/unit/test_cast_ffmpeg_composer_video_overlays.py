"""Composer composites non-bonded video overlays (stock track) into the graph.

Regression coverage for the bug where ``overlay_video_labels`` were declared as
FFmpeg inputs but never consumed in ``filter_complex`` — the stock track was
silently dropped at render time so only the avatar layer showed.

These are pure unit tests: they call ``translate_timeline_to_ffmpeg`` with a
hand-built timeline and assert on the emitted ``filter_complex`` string and the
input declarations. No DB / ORM, so the DB-free unit job runs them unchanged.
"""
import re

from services.cast_ffmpeg_composer import translate_timeline_to_ffmpeg


CANVAS_W = 1080
CANVAS_H = 1920


def _video_overlay_el(el_id, src, *, x, y, width, height, s, e):
    return {
        "id": el_id,
        "type": "video",
        "s": s,
        "e": e,
        "props": {
            "src": src,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        },
    }


def _timeline_with_overlays(overlays):
    return {"tracks": [{"type": "video", "id": "stock", "elements": overlays}]}


def test_two_overlays_produce_two_overlay_filter_steps():
    overlays = [
        _video_overlay_el("ov1", "https://ex/clip1.mp4",
                          x=0, y=424, width=1080, height=424, s=17.89, e=22.96),
        _video_overlay_el("ov2", "https://ex/clip2.mp4",
                          x=0, y=0, width=1080, height=1920, s=37.64, e=43.54),
    ]
    plan = translate_timeline_to_ffmpeg(
        _timeline_with_overlays(overlays), baked_urls={}, render_id="r1",
        canvas_width=CANVAS_W, canvas_height=CANVAS_H,
    )
    fc = plan.filter_complex
    # One overlay= compositing step per video overlay, each *defining* a fresh
    # ov_vid_N output label.
    defined_labels = re.findall(r"eof_action=pass\[(ov_vid_\d+)\]", fc)
    assert defined_labels == ["ov_vid_0", "ov_vid_1"]
    # Each overlay also gets a scale + setpts shift step.
    assert fc.count("scale=") >= 2
    assert fc.count("setpts=PTS-STARTPTS+") == 2


def test_overlay_filter_uses_props_geometry_not_frame():
    # Geometry must come from props.x/y/width/height. Put a decoy `frame`
    # block with different numbers to prove props wins.
    el = _video_overlay_el("ov1", "https://ex/clip.mp4",
                          x=12, y=424, width=600, height=400, s=5.0, e=9.0)
    el["frame"] = {"x": 999, "y": 999, "width": 1, "height": 1}
    plan = translate_timeline_to_ffmpeg(
        _timeline_with_overlays([el]), baked_urls={}, render_id="r1",
        canvas_width=CANVAS_W, canvas_height=CANVAS_H,
    )
    fc = plan.filter_complex
    # Scaled to props width/height.
    assert "scale=600:400" in fc
    # Overlaid at props x/y — and never at the decoy frame coords.
    assert "overlay=x=12:y=424" in fc
    assert "x=999" not in fc


def test_overlay_enable_window_matches_element_timing():
    el = _video_overlay_el("ov1", "https://ex/clip.mp4",
                          x=0, y=0, width=1080, height=1920, s=17.89, e=22.96)
    plan = translate_timeline_to_ffmpeg(
        _timeline_with_overlays([el]), baked_urls={}, render_id="r1",
        canvas_width=CANVAS_W, canvas_height=CANVAS_H,
    )
    fc = plan.filter_complex
    assert "enable='between(t,17.89,22.96)'" in fc
    # PTS shift uses the absolute start so the clip plays at its window.
    assert "setpts=PTS-STARTPTS+17.89/TB" in fc
    # eof_action=pass keeps the timeline intact for short overlay clips.
    assert "eof_action=pass" in fc


def test_overlay_audio_is_muted_on_input():
    el = _video_overlay_el("ov1", "https://ex/clip.mp4",
                          x=0, y=0, width=1080, height=1920, s=1.0, e=4.0)
    plan = translate_timeline_to_ffmpeg(
        _timeline_with_overlays([el]), baked_urls={}, render_id="r1",
        canvas_width=CANVAS_W, canvas_height=CANVAS_H,
    )
    ov_inputs = [i for i in plan.inputs if i.url == "https://ex/clip.mp4"]
    assert len(ov_inputs) == 1
    # Stock track is visual-only — audio must be dropped so it doesn't
    # duplicate over the avatar voice.
    assert ov_inputs[0].has_audio is False


def test_current_v_label_chain_is_intact_after_video_overlays():
    # The running video label (`current_v_label`) starts at `timeline_v`, then
    # each video overlay must consume the previous label and emit the next, so
    # the chain stays linear: timeline_v -> ov_vid_0 -> ov_vid_1.
    overlays = [
        _video_overlay_el("ov1", "https://ex/clip1.mp4",
                          x=0, y=424, width=1080, height=424, s=2.0, e=5.0),
        _video_overlay_el("ov2", "https://ex/clip2.mp4",
                          x=0, y=0, width=1080, height=1920, s=6.0, e=9.0),
    ]
    plan = translate_timeline_to_ffmpeg(
        _timeline_with_overlays(overlays), baked_urls={}, render_id="r1",
        canvas_width=CANVAS_W, canvas_height=CANVAS_H,
    )
    fc = plan.filter_complex
    # First overlay reads the timeline video label.
    assert re.search(r"\[timeline_v\]\[ov0_shifted\]overlay=.*\[ov_vid_0\]", fc)
    # Second overlay reads the first overlay's output, keeping the chain linear.
    assert re.search(r"\[ov_vid_0\]\[ov1_shifted\]overlay=.*\[ov_vid_1\]", fc)
    # The final composited video label is what downstream steps + the output
    # map consume — proving Step 3b's result is wired into the rest of the
    # graph rather than dropped.
    assert "[ov_vid_1]" in plan.output_map


def test_no_video_overlays_emits_no_overlay_steps():
    # Sanity: an empty stock track must not inject any ov_vid_* filter steps,
    # and the timeline video label flows straight through to the output map.
    plan = translate_timeline_to_ffmpeg(
        _timeline_with_overlays([]), baked_urls={}, render_id="r1",
        canvas_width=CANVAS_W, canvas_height=CANVAS_H,
    )
    assert "ov_vid_0" not in plan.filter_complex
    assert "[timeline_v]" in plan.output_map
