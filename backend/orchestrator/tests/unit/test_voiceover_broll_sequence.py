"""Unit tests for ``tasks.cast_render._bake_voiceover_broll_sequence`` and
``services.voiceover_broll.concat_video_clips``.

Bug: a voiceover block with 2+ ``parallel_media`` entries (a genuine
multi-shot B-roll cutaway with real ``start_offset_s``/``duration_s`` —
exactly what the editor timeline shows as separate "B-roll 1" / "B-roll 2"
chips, editorStarterMapping.ts) only ever showed the FIRST entry in the
final render. ``resolve_voiceover_visual_sources`` treats parallel_media as
a priority-ordered FALLBACK list (first candidate that bakes+validates wins
and covers the WHOLE slot; the rest are discarded, not sequenced) — correct
for its own retry purpose, wrong for a real multi-shot beat. Confirmed
against cst_294eeaf04af2 block blk_990214163387: two parallel_media video
entries at duration_s=3.5 each (start_offset_s 0.0 and 3.5) but the render
only ever showed the first.

These tests exercise the REAL ffmpeg path (no GPU/network — parallel_media
URLs are local file paths, downloaded via a monkeypatched ``_download``).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import types
from unittest.mock import AsyncMock

import pytest

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

from services import voiceover_broll
from tasks.cast_render import _bake_voiceover_broll_sequence


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; B-roll sequence tests need both",
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", *args], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")


def _probe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=60,
    )
    return float((proc.stdout or "0").strip() or 0.0)


def _make_clip(path: str, *, duration_s: float, color: str) -> None:
    _ffmpeg([
        "-f", "lavfi", "-i", f"color=c={color}:size=480x848:duration={duration_s}:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-t", f"{duration_s}", path,
    ])


class _FakeBlock:
    def __init__(self, parallel_media):
        self.parallel_media = parallel_media


def test_concat_video_clips_duration_is_sum_of_parts():
    with tempfile.TemporaryDirectory() as tmp:
        a = os.path.join(tmp, "a.mp4")
        b = os.path.join(tmp, "b.mp4")
        _make_clip(a, duration_s=3.5, color="red")
        _make_clip(b, duration_s=3.5, color="blue")
        with open(a, "rb") as fh:
            a_bytes = fh.read()
        with open(b, "rb") as fh:
            b_bytes = fh.read()

        out_bytes = _run(voiceover_broll.concat_video_clips([a_bytes, b_bytes]))
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as fh:
            fh.write(out_bytes)
        dur = _probe_duration(out_path)
        assert abs(dur - 7.0) < 0.3, (
            f"concatenated duration {dur:.2f}s should be ~7.0s (3.5 + 3.5)"
        )


def test_sequence_bakes_and_concatenates_both_entries(monkeypatch):
    """The regression case: 2 parallel_media video entries at 3.5s each
    (matching blk_990214163387) must both appear — the final clip's
    duration must reflect BOTH shots, not just the first one's slot-length
    stretch."""
    with tempfile.TemporaryDirectory() as tmp:
        clip_a = os.path.join(tmp, "shot1.mp4")
        clip_b = os.path.join(tmp, "shot2.mp4")
        _make_clip(clip_a, duration_s=2.0, color="red")
        _make_clip(clip_b, duration_s=2.0, color="blue")

        async def _fake_download(url, dest_path, *, timeout_s):
            shutil.copyfile(url, dest_path)

        monkeypatch.setattr(voiceover_broll, "_download", _fake_download)

        block = _FakeBlock(parallel_media=[
            {"kind": "video", "url": clip_a, "start_offset_s": 0.0, "duration_s": 3.5},
            {"kind": "video", "url": clip_b, "start_offset_s": 3.5, "duration_s": 3.5},
        ])
        session = types.SimpleNamespace(get=AsyncMock(return_value=block))

        broll_s = 7.0
        out_bytes = _run(_bake_voiceover_broll_sequence(
            "blk_test", session, broll_s, 480, 848, 30,
        ))
        assert out_bytes is not None, "sequence must bake when 2 valid entries exist"

        out_path = os.path.join(tmp, "seq.mp4")
        with open(out_path, "wb") as fh:
            fh.write(out_bytes)
        dur = _probe_duration(out_path)
        assert abs(dur - broll_s) < 0.5, (
            f"sequence duration {dur:.2f}s should span the full slot "
            f"{broll_s:.2f}s (both shots), not just one entry's share"
        )


def test_single_entry_returns_none_falls_through_to_candidate_loop(monkeypatch):
    """Only 1 usable parallel_media entry — nothing to sequence, so the
    caller's existing single-candidate retry loop must run unchanged."""
    block = _FakeBlock(parallel_media=[
        {"kind": "video", "url": "https://videos.pexels.com/a.mp4",
         "duration_s": 5.0, "start_offset_s": 0.0},
    ])
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))

    result = _run(_bake_voiceover_broll_sequence(
        "blk_test", session, 5.0, 480, 848, 30,
    ))
    assert result is None


def test_no_parallel_media_returns_none():
    block = _FakeBlock(parallel_media=None)
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))

    result = _run(_bake_voiceover_broll_sequence(
        "blk_test", session, 5.0, 480, 848, 30,
    ))
    assert result is None


def test_two_entries_one_failing_falls_through_to_none(monkeypatch):
    """One of only two entries fails to download — just 1 survivor is left,
    which isn't a sequence anymore, so the function must bail out (None)
    and let the caller's existing single-candidate loop try these same
    URLs as fallbacks instead of silently shipping a half-length clip."""
    with tempfile.TemporaryDirectory() as tmp:
        clip_ok = os.path.join(tmp, "ok.mp4")
        _make_clip(clip_ok, duration_s=2.0, color="green")

        async def _fake_download(url, dest_path, *, timeout_s):
            if "missing" in url:
                raise RuntimeError("simulated download failure")
            shutil.copyfile(url, dest_path)

        monkeypatch.setattr(voiceover_broll, "_download", _fake_download)

        block = _FakeBlock(parallel_media=[
            {"kind": "video", "url": clip_ok, "start_offset_s": 0.0, "duration_s": 3.5},
            {"kind": "video", "url": "https://videos.pexels.com/missing.mp4",
             "start_offset_s": 3.5, "duration_s": 3.5},
        ])
        session = types.SimpleNamespace(get=AsyncMock(return_value=block))

        result = _run(_bake_voiceover_broll_sequence(
            "blk_test", session, 7.0, 480, 848, 30,
        ))
        assert result is None


def test_three_entries_one_failing_redistributes_across_survivors(monkeypatch):
    """3 entries, 1 fails — the 2 survivors must be rescaled so the final
    concatenated clip still spans the full slot exactly (the Phase 2
    tolerance gate compares against audio_duration_s, not entry count)."""
    with tempfile.TemporaryDirectory() as tmp:
        clip_a = os.path.join(tmp, "a.mp4")
        clip_c = os.path.join(tmp, "c.mp4")
        _make_clip(clip_a, duration_s=2.0, color="green")
        _make_clip(clip_c, duration_s=2.0, color="yellow")

        async def _fake_download(url, dest_path, *, timeout_s):
            if "missing" in url:
                raise RuntimeError("simulated download failure")
            shutil.copyfile(url, dest_path)

        monkeypatch.setattr(voiceover_broll, "_download", _fake_download)

        block = _FakeBlock(parallel_media=[
            {"kind": "video", "url": clip_a, "start_offset_s": 0.0, "duration_s": 3.0},
            {"kind": "video", "url": "https://videos.pexels.com/missing.mp4",
             "start_offset_s": 3.0, "duration_s": 3.0},
            {"kind": "video", "url": clip_c, "start_offset_s": 6.0, "duration_s": 3.0},
        ])
        session = types.SimpleNamespace(get=AsyncMock(return_value=block))

        broll_s = 9.0
        out_bytes = _run(_bake_voiceover_broll_sequence(
            "blk_test", session, broll_s, 480, 848, 30,
        ))
        assert out_bytes is not None, (
            "2 of 3 entries survived — must still produce a sequence"
        )
        out_path = os.path.join(tmp, "seq.mp4")
        with open(out_path, "wb") as fh:
            fh.write(out_bytes)
        dur = _probe_duration(out_path)
        assert abs(dur - broll_s) < 0.5, (
            f"sequence duration {dur:.2f}s should still span the full slot "
            f"{broll_s:.2f}s after redistributing the failed entry's share"
        )
