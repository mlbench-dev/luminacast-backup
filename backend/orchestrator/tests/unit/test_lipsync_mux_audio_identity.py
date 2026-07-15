"""regression-2: the lipsync driver and the final mux must use the SAME
audio.

The lipsync engine is fed a loudness-normalized, edge-padded, silence-trimmed
WAV (``lipsync_audio_prep``); the mux historically laid down the un-shifted
master from the timeline A1 ``props.src``. Those differ by ~100ms onset plus
length, so lips lead the soundtrack on every block.

The fix rewrites every audio element's ``props.src`` to the recorded
lipsync-driver URL before compose, then asserts identity. These tests prove:

  1. ``_rewrite_mux_audio_to_lipsync`` repoints the A1 src to the exact URL
     the engine was driven with (path-(a) identity), leaving elements with
     no recorded driver untouched.
  2. ``_assert_lipsync_mux_audio_identity`` passes when driver == mux source
     and raises ``LipsyncMuxAudioDrift`` when they diverge beyond the gate.

The async assertion test uses real ffprobe against synthesized local files,
so ffmpeg/ffprobe must be on PATH; it skips otherwise. The pure rewrite test
needs neither.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

from tasks.cast_render import (
    LipsyncMuxAudioDrift,
    _assert_lipsync_mux_audio_identity,
    _rewrite_mux_audio_to_lipsync,
)


def _timeline_with_audio(*, block_id: str, master_src: str, music_src: str) -> dict:
    """One audio track with a speaking-block A1 element (carries
    metadata.block_id) plus a music element (no block_id → must be left
    alone by the rewrite)."""
    return {
        "tracks": [
            {
                "type": "video",
                "elements": [
                    {"id": "v0", "s": 0, "e": 4,
                     "metadata": {"block_id": block_id, "bonded": True},
                     "props": {"src": "https://cdn/face.mp4"}},
                ],
            },
            {
                "type": "audio",
                "elements": [
                    {"id": "a0", "s": 0, "e": 4,
                     "metadata": {"block_id": block_id},
                     "props": {"src": master_src}},
                    {"id": "music0", "s": 0, "e": 4,
                     "metadata": {},
                     "props": {"src": music_src}},
                ],
            },
        ],
    }


def test_rewrite_points_mux_src_at_lipsync_driver():
    """The A1 element for the block must end up pointing at the exact URL
    the lipsync engine was driven with; the music element (no recorded
    driver) is left untouched."""
    driver_url = "https://cdn/lipsync_prep/rnd_1/blk_0-abc.wav"
    master_url = "https://cdn/tts/voice/123.mp3"
    music_url = "https://cdn/music/track.mp3"
    timeline = _timeline_with_audio(
        block_id="blk_0", master_src=master_url, music_src=music_url,
    )

    n = _rewrite_mux_audio_to_lipsync(
        timeline,
        lipsync_audio_by_block={"blk_0": driver_url},
        render_id="rnd_1",
    )

    audio_els = timeline["tracks"][1]["elements"]
    a0 = next(e for e in audio_els if e["id"] == "a0")
    music = next(e for e in audio_els if e["id"] == "music0")

    assert n == 1, "exactly the one speaking-block element should be rewritten"
    assert a0["props"]["src"] == driver_url, (
        "mux source must be identical to the lipsync driver URL"
    )
    assert music["props"]["src"] == music_url, "music element must be untouched"


def test_rewrite_is_noop_without_recorded_drivers():
    """No recorded driver URLs → nothing is rewritten."""
    timeline = _timeline_with_audio(
        block_id="blk_0", master_src="https://cdn/m.mp3", music_src="https://cdn/x.mp3",
    )
    n = _rewrite_mux_audio_to_lipsync(
        timeline, lipsync_audio_by_block={}, render_id="rnd_1",
    )
    assert n == 0
    assert timeline["tracks"][1]["elements"][0]["props"]["src"] == "https://cdn/m.mp3"


# ── async identity assertion (needs ffprobe) ──────────────────────────────

def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _synth(out_path: str, duration_s: float) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"sine=frequency=300:duration={duration_s}",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"synth failed: {proc.stderr[-300:]}")


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_assert_passes_when_identical_and_fails_on_drift():
    tmp = tempfile.mkdtemp(prefix="ls_identity_")
    driver = os.path.join(tmp, "driver.wav")
    longer = os.path.join(tmp, "longer.wav")
    _synth(driver, 4.0)
    _synth(longer, 4.5)  # 500ms longer → far beyond the 30ms gate

    # Identical src on both sides (the post-rewrite state) → passes.
    tl_ok = _timeline_with_audio(
        block_id="blk_0", master_src=driver, music_src="https://cdn/music.mp3",
    )
    asyncio.run(
        _assert_lipsync_mux_audio_identity(
            tl_ok,
            lipsync_audio_by_block={"blk_0": driver},
            render_id="rnd_ok",
        )
    )

    # Mux src diverges from the driver by 500ms → must raise.
    tl_bad = _timeline_with_audio(
        block_id="blk_0", master_src=longer, music_src="https://cdn/music.mp3",
    )
    with pytest.raises(LipsyncMuxAudioDrift):
        asyncio.run(
            _assert_lipsync_mux_audio_identity(
                tl_bad,
                lipsync_audio_by_block={"blk_0": driver},
                render_id="rnd_bad",
            )
        )
