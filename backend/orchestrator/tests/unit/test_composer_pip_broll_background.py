"""A full-frame b-roll on a talking-head / split block is its BACKGROUND.

Bug: for a pip_talking_head (pip_quarter) block the composer baked the face
onto flat black, then Step 3b laid the full-frame b-roll on top — hiding the
talking head entirely in the final render. The b-roll must instead be the PIP
base (behind the face) and NOT re-applied as a top overlay.

Pure unit tests: build a timeline by hand, call translate_timeline_to_ffmpeg,
assert on the emitted filter_complex. No DB / network / ffmpeg.
"""
from services.cast_ffmpeg_composer import translate_timeline_to_ffmpeg

CANVAS_W = 1080
CANVAS_H = 1920
TOTAL = 6.0


def _timeline(*, pip_layout, broll_props):
    """One bonded segment (optionally PIP) + one b-roll on the stock track,
    both for block 'blk1'."""
    v1_meta = {"block_id": "blk1", "bonded": True, "paired_audio_element_id": "a1_blk1"}
    if pip_layout:
        v1_meta["pip_layout"] = pip_layout
    return {
        "tracks": [
            {"id": "video", "type": "video", "elements": [{
                "id": "v1_blk1", "type": "video", "s": 0.0, "e": TOTAL,
                "props": {"src": "https://x/seg.mp4"}, "metadata": v1_meta,
            }]},
            {"id": "voice", "type": "audio", "elements": [{
                "id": "a1_blk1", "type": "audio", "s": 0.0, "e": TOTAL,
                "props": {"src": "https://x/voice.mp3"},
                "metadata": {"block_id": "blk1", "bonded": True,
                             "paired_video_element_id": "v1_blk1"},
            }]},
            {"id": "stock", "type": "video", "elements": [{
                "id": "ov_broll", "type": "video", "s": 0.0, "e": TOTAL,
                "props": {"src": "https://x/broll.mp4", **broll_props},
                "metadata": {"block_id": "blk1"},
            }]},
        ]
    }


_BAKED = {"v1_blk1": "https://x/baked.mp4"}
_FULL_FRAME = {"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H}


def _plan(tl):
    return translate_timeline_to_ffmpeg(tl, _BAKED, "r1", CANVAS_W, CANVAS_H)


def test_full_frame_broll_becomes_the_pip_base_not_a_top_overlay():
    fc = _plan(_timeline(pip_layout="pip_small", broll_props=_FULL_FRAME)).filter_complex
    # b-roll (input index 1) is cover-fit to canvas and feeds the pip base…
    assert (
        f"[1:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{CANVAS_H},setsar=1[pip0_base]"
    ) in fc
    # …instead of the flat-black base…
    assert f"color=c=black:s={CANVAS_W}x{CANVAS_H}:d=1[pip0_base]" not in fc
    # …and it is NOT also composited as a Step-3b overlay over the face.
    assert "[ov_vid_0]" not in fc
    assert "eof_action=pass" not in fc


def test_split_h_block_also_uses_broll_as_base():
    fc = _plan(_timeline(pip_layout="split_h", broll_props=_FULL_FRAME)).filter_complex
    assert "[pip0_split_base]" in fc
    assert f"color=c=black:s={CANVAS_W}x{CANVAS_H}:d=1[pip0_split_base]" not in fc
    assert "[1:v]scale=" in fc and "[pip0_split_base]" in fc
    assert "[ov_vid_0]" not in fc


def test_partial_broll_on_pip_block_stays_a_normal_overlay():
    # A half-frame cutaway is NOT the background — it must still overlay.
    fc = _plan(_timeline(
        pip_layout="pip_small",
        broll_props={"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H // 2},
    )).filter_complex
    assert f"color=c=black:s={CANVAS_W}x{CANVAS_H}:d=1[pip0_base]" in fc
    assert "eof_action=pass[ov_vid_0]" in fc


def test_full_frame_broll_on_fullscreen_block_is_unchanged():
    # No PIP → legacy behaviour: b-roll is a normal top overlay, no pip base.
    fc = _plan(_timeline(pip_layout=None, broll_props=_FULL_FRAME)).filter_complex
    assert "pip0_base" not in fc
    assert "eof_action=pass[ov_vid_0]" in fc
