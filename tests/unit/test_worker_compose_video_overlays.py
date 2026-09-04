"""Unit tests for stock video overlay compositing in worker_ffmpeg_compose.

Regression coverage for PR #148: stock (non-bonded) video overlays downloaded
but never composited onto the rendered mp4 because the production composer
(``worker_ffmpeg_compose._run_ffmpeg_compose``) built the video overlay branch
with ``format=yuva420p`` + ``tpad start_mode=add:color=0x00000000``, which the
production FFmpeg build rendered as opaque black / dropped silently. The fix
mirrors the proven approach: ``scale=W:H,setpts=PTS-STARTPTS+{start}/TB`` then
``overlay=x:y:enable='between(t,start,end)':eof_action=pass``.

These tests inspect the constructed filtergraph directly via the extracted
``_build_overlay_filter_parts`` helper, so no real FFmpeg invocation or network
download is needed. A fake ``_download`` is provided to document the stubbing
contract used by the wider compose path.
"""

import os
import sys

# worker_ffmpeg_compose lives at the repo root; make it importable regardless of
# where pytest is invoked from.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import worker_ffmpeg_compose as wfc


def _fake_download(url, dest, timeout):
    """Stand-in for ``_download`` that writes a tiny placeholder file.

    Returns a byte count like the real downloader so callers that log size do
    not blow up. No network access.
    """
    with open(dest, "wb") as fh:
        fh.write(b"\x00" * 16)
    return 16


def _video_overlay(idx=1, x=0, y=0, w=480, h=848, start=10.0, end=15.0):
    return {
        "type": "video",
        "src": "https://example.com/pexels-stock.mp4",
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "start_s": start,
        "end_s": end,
        "_input_index": idx,
    }


def test_video_overlay_uses_setpts_and_eof_action_pass():
    """Fullscreen stock overlay scales to geometry, time-shifts via setpts,
    and overlays with the enable window — no tpad, no format=yuva420p."""
    ov = _video_overlay(idx=1, x=0, y=0, w=480, h=848, start=10.0, end=15.0)

    parts, current_label = wfc._build_overlay_filter_parts([ov], "[0:v]")
    fc = ";".join(parts)

    # Scale to the overlay geometry (fullscreen 480x848) + PTS time-shift.
    assert "scale=480:848,setpts=PTS-STARTPTS+10.0/TB[scaled1]" in fc
    # Overlay at (0,0) gated to the visibility window, surviving short clips.
    assert "overlay=0:0:enable='between(t,10.0,15.0)':eof_action=pass" in fc
    # The fragile pre-fix constructs must be gone for video overlays.
    assert "tpad" not in fc
    assert "format=yuva420p" not in fc
    assert "shortest=0" not in fc
    # Chain advances to the overlay output pad.
    assert current_label == "[img1]"


def test_video_overlay_split_geometry():
    """Split-screen overlay (480x424 @ 0,424) keeps its non-zero offset."""
    ov = _video_overlay(idx=2, x=0, y=424, w=480, h=424, start=37.63, end=43.53)

    parts, _ = wfc._build_overlay_filter_parts([ov], "[0:v]")
    fc = ";".join(parts)

    assert "scale=480:424,setpts=PTS-STARTPTS+37.63/TB[scaled2]" in fc
    assert "overlay=0:424:enable='between(t,37.63,43.53)':eof_action=pass" in fc


def test_image_overlay_keeps_tpad_path():
    """Image overlays must retain the alpha-aware tpad path (unchanged)."""
    ov = {
        "type": "image",
        "src": "https://example.com/logo.png",
        "x": 20,
        "y": 20,
        "width": 100,
        "height": 100,
        "start_s": 5.0,
        "end_s": 9.0,
        "_input_index": 1,
    }

    parts, _ = wfc._build_overlay_filter_parts([ov], "[0:v]")
    fc = ";".join(parts)

    assert "format=yuva420p" in fc
    assert "tpad=start_duration=5.0:start_mode=add:color=0x00000000" in fc
    # Images do not use the setpts time-shift or eof_action=pass guard.
    assert "setpts=PTS-STARTPTS+5.0/TB" not in fc
    assert "eof_action=pass" not in fc


def test_video_and_image_overlays_chain_together():
    """A video overlay followed by an image overlay should chain output pads
    so both composite on top of the base canvas."""
    vid = _video_overlay(idx=1, x=0, y=0, w=480, h=848, start=10.0, end=15.0)
    img = {
        "type": "image",
        "src": "https://example.com/logo.png",
        "x": 20,
        "y": 20,
        "width": 100,
        "height": 100,
        "start_s": 2.0,
        "end_s": 4.0,
        "_input_index": 2,
    }

    parts, current_label = wfc._build_overlay_filter_parts([vid, img], "[0:v]")
    fc = ";".join(parts)

    # Video overlay consumes the base and emits [img1]; image overlay consumes
    # [img1] and emits [img2].
    assert "[0:v][scaled1]overlay=0:0" in fc
    assert "[img1][scaled2]overlay=20:20" in fc
    assert current_label == "[img2]"


def test_overlay_missing_input_index_is_skipped():
    """Overlays without an assigned input index (download failed) are skipped
    so a fetch failure cannot inject a dangling filter reference."""
    ov = _video_overlay()
    del ov["_input_index"]

    parts, current_label = wfc._build_overlay_filter_parts([ov], "[0:v]")

    assert parts == []
    assert current_label == "[0:v]"


def test_fake_download_contract():
    """The fake downloader writes a file and returns a byte count, matching the
    real ``_download`` signature used by the compose path."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "stock.mp4")
        size = _fake_download("https://example.com/x.mp4", dest, timeout=30)
        assert size == 16
        assert os.path.exists(dest)


def test_video_overlay_with_fit_cover():
    """Video overlay with fit=cover should use aspect-preserving scale+crop."""
    ov = _video_overlay(idx=1, x=0, y=0, w=1920, h=1080, start=5.0, end=10.0)
    ov["fit"] = "cover"

    parts, current_label = wfc._build_overlay_filter_parts([ov], "[0:v]")
    fc = ";".join(parts)

    assert "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1" in fc
    assert "setpts=PTS-STARTPTS+5.0/TB[scaled1]" in fc
    assert "overlay=0:0:enable='between(t,5.0,10.0)':eof_action=pass[img1]" in fc


def test_video_overlay_with_fit_contain():
    """Video overlay with fit=contain should use aspect-preserving scale+pad."""
    ov = _video_overlay(idx=1, x=0, y=0, w=1920, h=1080, start=5.0, end=10.0)
    ov["fit"] = "contain"

    parts, current_label = wfc._build_overlay_filter_parts([ov], "[0:v]")
    fc = ";".join(parts)

    assert "format=rgba,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1" in fc
    assert "setpts=PTS-STARTPTS+5.0/TB[scaled1]" in fc
    assert "overlay=0:0:enable='between(t,5.0,10.0)':eof_action=pass[img1]" in fc

