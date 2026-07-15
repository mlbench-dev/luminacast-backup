"""Integration-style test: the composed timeline must have no boundary blacks.

Background: the slot-aligned compose lays each baked clip onto a black canvas
gated by ``enable='between(t, s, e)'``. When a clip's slot ends before the next
clip's slot starts (``e[i] < s[i+1]``) the black canvas used to show through the
gap, producing 0.4-0.7s black runs at every block boundary (render
``rnd_124ec1f95740``). The fix extends each clip's gate to the next clip's start
and holds its last frame across the gap with ``tpad=stop_mode=clone``.

This test builds three synthetic clips whose slots leave deliberate gaps, runs
``_run_ffmpeg_compose`` end to end (downloads and the R2 upload mocked, ffmpeg
real), then runs blackdetect on the produced mp4 and asserts there are no
internal black runs longer than the threshold. It also asserts the pre-fix graph
would have failed, to guard against a silent revert.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

# ─── Stub sentry_sdk so importing the module doesn't pull the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

# The compose module lives at the orchestrator package root.
_ORCH_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORCH_ROOT))

import worker_ffmpeg_compose as wfc  # noqa: E402


_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(
    not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available"
)


def _make_clip(path: str, color: str, dur: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=240x240:r=30:d={dur:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
        ],
        check=True,
    )


# Three clips placed on the timeline with deliberate gaps between their slots:
#   clip0: [0.0, 3.0]            gap 3.0-3.5
#   clip1: [3.5, 6.0]            gap 6.0-7.0
#   clip2: [7.0, 9.0]            (last)
# timeline_dur = 9.0. Without the boundary-black fix the canvas shows through
# the two gaps as ~0.5s and ~1.0s black runs.
_CLIPS = [
    ("c0", "red", 0.0, 3.0),
    ("c1", "green", 3.5, 6.0),
    ("c2", "blue", 7.0, 9.0),
]
_TIMELINE_DUR = 9.0


class _Req:
    """Minimal stand-in for the render request object the worker consumes."""

    def __init__(self, render_id, timeline, video_tracks):
        self.render_id = render_id
        self.timeline = timeline
        self.compose_video_tracks = video_tracks
        self.compose_audio_tracks = []
        self.baked_urls = {}
        self.overlay_elements = []


def _build_request_and_sources(tmp_path: Path):
    """Create the synthetic source clips and a request referencing them by url."""
    sources: dict[str, str] = {}
    video_tracks = []
    for cid, color, s, e in _CLIPS:
        src = str(tmp_path / f"{cid}.mp4")
        _make_clip(src, color, e - s)
        url = f"https://example.test/{cid}.mp4"
        sources[url] = src
        video_tracks.append({"url": url, "s": s, "e": e, "block_id": cid})

    timeline = {
        "compositionWidth": 240,
        "compositionHeight": 240,
        "fps": 30,
        "tracks": [
            {
                "elements": [
                    {"s": s, "e": e} for _cid, _c, s, e in _CLIPS
            ]
            }
        ],
    }
    req = _Req("rnd_test_boundary", timeline, video_tracks)
    return req, sources


def _run_compose(monkeypatch, tmp_path: Path):
    """Run the real compose with downloads + R2 upload mocked out."""
    req, sources = _build_request_and_sources(tmp_path)

    def fake_download(url, dest, timeout):
        shutil.copyfile(sources[url], dest)
        return os.path.getsize(dest)

    captured = {}

    class _FakeS3:
        def upload_file(self, local_path, bucket, key, ExtraArgs=None):
            # Keep the final mp4 around so the test can blackdetect it.
            kept = str(tmp_path / "final_kept.mp4")
            shutil.copyfile(local_path, kept)
            captured["final"] = kept

    monkeypatch.setattr(wfc, "_download", fake_download)
    monkeypatch.setattr(wfc, "_get_s3", lambda: _FakeS3())

    wfc._run_ffmpeg_compose(req)
    return captured["final"]


def test_compose_has_no_internal_boundary_black(monkeypatch, tmp_path):
    final = _run_compose(monkeypatch, tmp_path)
    runs = wfc._detect_internal_black_runs(final, _TIMELINE_DUR)
    assert runs == [], (
        f"composite has internal black runs > {wfc._MAX_INTERNAL_BLACK_S}s "
        f"at block boundaries: {runs}"
    )


def test_detect_helper_flags_a_gapped_graph(monkeypatch, tmp_path):
    """Sanity check that the detector itself catches a known-black composite.

    Builds the pre-fix gated graph by hand (own-slot enable windows, no tail
    hold) and asserts blackdetect reports the two boundary gaps. This guards
    against the detector silently returning [] for everything.
    """
    req, sources = _build_request_and_sources(tmp_path)
    canvas = str(tmp_path / "gapped.mp4")
    inputs = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{_TIMELINE_DUR:.3f}",
        "-i", "color=c=black:s=240x240:r=30",
    ]
    for t in req.compose_video_tracks:
        inputs += ["-i", sources[t["url"]]]
    parts = []
    current = "[0:v]"
    for i, t in enumerate(req.compose_video_tracks):
        pre = f"v{i}p"
        out = f"v{i}o"
        parts.append(f"[{i + 1}:v]setpts=PTS-STARTPTS+{t['s']:.3f}/TB[{pre}]")
        parts.append(
            f"{current}[{pre}]overlay=0:0:eof_action=pass:"
            f"enable='between(t,{t['s']:.3f},{t['e']:.3f})'[{out}]"
        )
        current = f"[{out}]"
    inputs += [
        "-filter_complex", ";".join(parts), "-map", current,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-t", f"{_TIMELINE_DUR:.3f}", canvas,
    ]
    subprocess.run(inputs, check=True)

    runs = wfc._detect_internal_black_runs(canvas, _TIMELINE_DUR)
    assert len(runs) == 2, f"expected two boundary blacks, got {runs}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
