"""regression-2: prove the voice post-process chain is duration-preserving.

If the audio fed to the lipsync engine and the audio muxed into the final
video differ in length, lips drift from the soundtrack on every block. The
fix pins both to the SAME audio; this test guards the precondition that
``post_process_voice`` does not itself retime the signal — the EQ + compand
+ single-pass loudnorm chain is gain-only, so each output must match the
input duration within a tight tolerance.

ffmpeg + ffprobe must be on PATH; tests skip otherwise.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from services.media_processing import post_process_voice


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; duration-preservation test needs both",
)


def _synth_wav(*, out_path: str, duration_s: float) -> None:
    """Synthesise a known-duration mono WAV (16 kHz PCM) standing in for a
    raw TTS clip. WAV (not MP3) so the input duration is exact, not subject
    to encoder padding."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration_s}",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"synth wav ffmpeg failed: {proc.stderr[-500:]}")


def _probe_duration_s(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-500:]}")
    fmt = (json.loads(proc.stdout or "{}").get("format") or {})
    return float(fmt.get("duration") or 0)


@pytest.mark.parametrize("clip_mic_enabled", [False, True])
def test_post_process_preserves_duration(clip_mic_enabled: bool):
    """Both outputs (16k WAV lipsync feed + 44.1k MP3 mix master) must
    preserve the input duration within ±5ms, so the lipsync driver and the
    mux master stay sample-aligned."""
    tmp = tempfile.mkdtemp(prefix="ppv_dur_")
    in_path = os.path.join(tmp, "in.wav")
    out_lipsync = os.path.join(tmp, "out.lipsync.wav")
    out_mix = os.path.join(tmp, "out.mix.mp3")

    in_dur = 4.0
    _synth_wav(out_path=in_path, duration_s=in_dur)
    measured_in = _probe_duration_s(in_path)

    lip, mix = asyncio.run(
        post_process_voice(
            in_path, out_lipsync, out_mix,
            clip_mic_enabled=clip_mic_enabled,
            block_id="blk_dur",
        )
    )
    assert lip == out_lipsync and mix == out_mix

    lip_dur = _probe_duration_s(lip)
    mix_dur = _probe_duration_s(mix)

    # WAV lipsync feed: PCM, no encoder padding. This is the track that
    # actually drives the lipsync engine AND (post-fix) the mux, so it must
    # be sample-accurate — tight ±5ms bound proves the EQ/compand/loudnorm
    # chain does not retime the signal.
    assert abs(lip_dur - measured_in) <= 0.005, (
        f"lipsync WAV duration {lip_dur:.4f}s drifted from input "
        f"{measured_in:.4f}s by {(lip_dur - measured_in) * 1000:.1f}ms"
    )
    # MP3 mix master: LAME's container reports duration inflated by the
    # encoder's fixed start/end padding (a couple of frames, ~50ms) — this
    # is metadata, NOT signal retiming, and the decoder strips it on
    # playback. The lipsync fix muxes the PCM WAV (asserted above) rather
    # than this MP3, so this bound only guards against gross retiming.
    assert abs(mix_dur - measured_in) <= 0.075, (
        f"mix MP3 duration {mix_dur:.4f}s drifted from input "
        f"{measured_in:.4f}s by {(mix_dur - measured_in) * 1000:.1f}ms"
    )


@pytest.mark.parametrize("scene_chain_id", ["ambient_room_soft", "ambient_outdoor"])
def test_post_process_scene_chains_preserve_duration(scene_chain_id: str):
    """The room / outdoor scene chains add longer reflection taps than the
    studio chain (up to ~115ms). The trailing atrim must still pin both
    outputs to the input length so the lipsync feed stays sample-aligned."""
    tmp = tempfile.mkdtemp(prefix="ppv_scene_dur_")
    in_path = os.path.join(tmp, "in.wav")
    out_lipsync = os.path.join(tmp, "out.lipsync.wav")
    out_mix = os.path.join(tmp, "out.mix.mp3")

    in_dur = 4.0
    _synth_wav(out_path=in_path, duration_s=in_dur)
    measured_in = _probe_duration_s(in_path)

    lip, mix = asyncio.run(
        post_process_voice(
            in_path, out_lipsync, out_mix,
            clip_mic_enabled=False,
            scene_chain_id=scene_chain_id,
            block_id="blk_scene_dur",
        )
    )
    # A drift beyond the guard makes post_process_voice fall back to the
    # raw input; assert it produced real, distinct output files instead.
    assert lip == out_lipsync and mix == out_mix

    assert abs(_probe_duration_s(lip) - measured_in) <= 0.005
    assert abs(_probe_duration_s(mix) - measured_in) <= 0.075
