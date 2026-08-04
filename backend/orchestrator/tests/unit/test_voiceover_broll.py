"""Unit tests for voiceover-block B-roll (eliminates the 7s black slot).

Background: ``render_mode=voiceover`` blocks carry narration audio but no
face track. The dispatcher used to return ``None`` for the video side, so
the FFmpeg compose pass found no clip for the slot and laid down pure black
behind the audio — the 7.27s black at 40.23-47.50 on rnd_124ec1f95740
(compose reported videos=9 vs audios=10).

The fix routes voiceover blocks through ``services.voiceover_broll``, which
fills the slot with motion-bearing B-roll (a product video looped to length,
or a product/scene/avatar image animated with a slow Ken Burns pan-zoom).

These tests exercise the REAL ffmpeg path (no GPU/network) and assert the
shipped contract:

  * a voiceover B-roll clip's duration matches the audio slot, and
  * it passes the Phase 3 ``clip_mostly_frozen`` validator (Ken Burns motion),
    whereas a pure-still clip of the same image is REJECTED as frozen —
    proving the motion is what saves it;
  * the dispatch branch in cast_render.py bakes + uploads instead of
    returning ``None``.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest


# Stub sentry_sdk so importing the service module doesn't pull the real dep.
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

from services import voiceover_broll
from services.media_processing import validate_baked_clip, ClipValidationReason


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; voiceover B-roll tests need both",
)

ORCH_ROOT = Path(__file__).resolve().parents[2]
CAST_RENDER_PATH = ORCH_ROOT / "tasks" / "cast_render.py"


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


def _make_image(path: str) -> None:
    """A detailed still image (testsrc2 single frame) for Ken Burns input."""
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=1",
        "-frames:v", "1", path,
    ])


def _serve_via_file_url(path: str) -> str:
    # httpx in the service follows redirects and accepts file:// only via a
    # transport; simplest is to monkeypatch the downloader to copy locally.
    return path


def test_ken_burns_clip_matches_slot_and_passes_freeze_validator(monkeypatch):
    """The headline test: a voiceover image → Ken Burns clip of slot length
    that survives clip_mostly_frozen, then carries the narration audio and
    passes the full Phase 3 validator (motion + audio present)."""
    slot_s = 7.27  # the exact black-slot duration from rnd_124ec1f95740

    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, "hero.png")
        _make_image(img)

        # The service downloads via httpx; short-circuit to a local copy so
        # the test needs no network.
        async def _fake_download(url, dest_path, *, timeout_s):
            shutil.copyfile(url, dest_path)

        monkeypatch.setattr(voiceover_broll, "_download", _fake_download)

        video_bytes = _run(voiceover_broll.render_ken_burns_from_image(
            image_url=img, slot_s=slot_s, width=1080, height=1920, fps=30,
        ))
        assert video_bytes, "Ken Burns render returned no bytes"

        clip = os.path.join(tmp, "broll.mp4")
        with open(clip, "wb") as fh:
            fh.write(video_bytes)

        # Duration matches the slot (the audio source of truth).
        dur = _probe_duration(clip)
        assert abs(dur - slot_s) < 0.3, (
            f"B-roll duration {dur:.2f}s should match slot {slot_s:.2f}s"
        )

        # Silent Ken Burns clip already passes the freeze gate (motion), but
        # the voiceover block requires audio — confirm the no-audio reason is
        # the ONLY thing missing (i.e. video is NOT frozen / black).
        reason_silent = _run(validate_baked_clip(
            clip, block_id="blk", render_id="rnd", require_audio=False,
        ))
        assert reason_silent == ClipValidationReason.OK, (
            f"Ken Burns clip must pass freeze/black checks; got {reason_silent}"
        )

        # Now mux a narration track (sine stands in for TTS) and validate the
        # full contract: motion + audio present.
        narrated = os.path.join(tmp, "narrated.mp4")
        _ffmpeg([
            "-i", clip,
            "-f", "lavfi", "-i", f"sine=frequency=330:duration={slot_s}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-ar", "48000", "-ac", "2", "-shortest", narrated,
        ])
        reason = _run(validate_baked_clip(
            narrated, block_id="blk", render_id="rnd", require_audio=True,
        ))
        assert reason == ClipValidationReason.OK, (
            f"narrated voiceover B-roll must pass Phase 3; got {reason}"
        )


def test_pure_still_of_same_image_is_rejected_as_frozen(monkeypatch):
    """Control: a STILL clip of the same image (no Ken Burns) is rejected by
    clip_mostly_frozen — proving the pan-zoom motion is what makes the
    voiceover B-roll valid, not the image itself."""
    slot_s = 4.0
    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, "hero.png")
        _make_image(img)
        still = os.path.join(tmp, "still.mp4")
        # Looped still frame → frozen video.
        _ffmpeg([
            "-loop", "1", "-i", img, "-t", f"{slot_s}",
            "-vf", "scale=1080:1920,fps=30", "-c:v", "libx264",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", still,
        ])
        reason = _run(validate_baked_clip(
            still, block_id="blk", render_id="rnd", require_audio=False,
        ))
        assert reason == ClipValidationReason.MOSTLY_FROZEN, (
            f"a pure still must be rejected as frozen; got {reason}"
        )


def test_video_to_slot_loops_short_source_to_fill_slot(monkeypatch):
    """A product video shorter than the slot is looped to fill it (no black
    tail) and the result matches the slot duration."""
    slot_s = 6.0
    with tempfile.TemporaryDirectory() as tmp:
        short = os.path.join(tmp, "short.mp4")
        _ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=480x848:duration=2:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-t", "2", short,
        ])

        async def _fake_download(url, dest_path, *, timeout_s):
            shutil.copyfile(url, dest_path)

        monkeypatch.setattr(voiceover_broll, "_download", _fake_download)

        out = _run(voiceover_broll.render_video_to_slot(
            video_url=short, slot_s=slot_s, width=1080, height=1920, fps=30,
        ))
        clip = os.path.join(tmp, "looped.mp4")
        with open(clip, "wb") as fh:
            fh.write(out)
        dur = _probe_duration(clip)
        assert abs(dur - slot_s) < 0.3, (
            f"looped video duration {dur:.2f}s should fill slot {slot_s:.2f}s"
        )


# ─── Static-code assertions on the dispatch branch ───────────────────────────

def _voiceover_branch(src: str) -> str:
    start = src.find('if block_render_mode == "voiceover":')
    assert start != -1, "could not locate voiceover branch in cast_render.py"
    end = src.find("# ── avatar_motion (T2V)", start)
    assert end != -1, "could not locate end of voiceover branch"
    return src[start:end]


def test_voiceover_branch_bakes_instead_of_returning_none():
    """The branch must render B-roll, validate it, and upload to R2 —
    not short-circuit to ``return primary_id, None`` on the happy path."""
    src = CAST_RENDER_PATH.read_text(encoding="utf-8")
    branch = _voiceover_branch(src)

    assert "voiceover_broll" in branch, "branch must invoke the B-roll service"
    assert "resolve_voiceover_visual_source(" in branch, (
        "branch must resolve a visual source (video/image) for the slot"
    )
    assert "resolve_avatar_idle_image(" in branch, (
        "branch must fall back to avatar idle B-roll as a last resort"
    )
    assert "_validate_baked_clip_bytes(" in branch, (
        "branch must gate the B-roll on the Phase 3 validators"
    )
    assert "r2.upload_bytes(video_bytes, baked_key" in branch, (
        "branch must bake to renders/{id}/baked_blocks/v1_{block}.mp4"
    )
    # The happy path returns the baked key, not None.
    assert "return primary_id, baked_key" in branch


def test_voiceover_branch_registers_audio_for_stale_src_rewrite():
    """Bug: voiceover blocks have no lipsync feed, so they were never covered
    by _rewrite_mux_audio_to_lipsync's post-bake refresh of the compose-time
    A1 element's props.src — that rewrite only fires for block_ids present in
    lipsync_audio_by_block. Confirmed against a real cast: a voiceover
    block's saved timeline audio src pointed at an 8.4s-old TTS file while
    the live variant's current audio was a different 6.5s file — two
    different sentences, one baked into the clip and a stale different one
    played separately at compose time, overlapping the next block's
    narration and sounding like two voices talking at once.

    The branch must record its audio_url into lipsync_audio_by_block (the
    same dict the lipsync rewrite step reads) so its A1 element gets
    refreshed to the CURRENT audio just like lipsync-driven blocks do."""
    src = CAST_RENDER_PATH.read_text(encoding="utf-8")
    branch = _voiceover_branch(src)
    assert "lipsync_audio_by_block[block_id] = audio_url" in branch, (
        "voiceover branch must register its audio_url so the post-bake "
        "rewrite step refreshes the compose-time A1 src instead of leaving "
        "a stale Arrange-timeline URL in place"
    )


def test_voiceover_branch_has_no_engine_names_in_user_strings():
    """progress_step strings must stay free of engine names (RULES.md)."""
    src = CAST_RENDER_PATH.read_text(encoding="utf-8")
    branch = _voiceover_branch(src)
    progress_matches = re.findall(r'progress_step\s*=\s*[fr]?"([^"]*)"', branch)
    forbidden = ("infinitetalk", "musetalk", "wavespeed", "hostkey", "wan",
                 "hallo", "kling", "ken burns", "ken-burns", "zoompan")
    for line in progress_matches:
        lower = line.lower()
        for name in forbidden:
            assert name not in lower, (
                f"progress_step {line!r} must not name {name!r}"
            )
