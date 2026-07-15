"""Unit tests for services.media_processing.post_process_voice.

Synthesise TTS-like inputs via lavfi so the tests need no GPU, no
faster-whisper, no network. ffmpeg + ffprobe must be on PATH; tests
skip otherwise.
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
    reason="ffmpeg/ffprobe not on PATH; post_process_voice tests need both",
)


def _synth_sine(*, out_path: str, duration_s: float = 3.0) -> None:
    """Synthesise a stereo sine-wave MP3 that mimics a raw TTS clip."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"sine=frequency=300:duration={duration_s}",
        "-codec:a", "libmp3lame", "-b:a", "128k",
        "-ar", "44100", "-ac", "1",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"synth sine ffmpeg failed: {proc.stderr[-500:]}")


def _probe(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-500:]}")
    info = json.loads(proc.stdout or "{}")
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration_s": float(fmt.get("duration") or 0),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "codec_name": audio.get("codec_name") or "",
    }


def _run_dual_output(*, clip_mic_enabled: bool) -> tuple[str, str, dict, dict]:
    tmp = tempfile.mkdtemp(prefix="ppv_test_")
    in_path = os.path.join(tmp, "in.mp3")
    out_lipsync = os.path.join(tmp, "out.lipsync.wav")
    out_mix = os.path.join(tmp, "out.mix.mp3")
    _synth_sine(out_path=in_path, duration_s=3.0)

    lip, mix = asyncio.run(
        post_process_voice(
            in_path,
            out_lipsync,
            out_mix,
            clip_mic_enabled=clip_mic_enabled,
            block_id=f"blk_ppv_{'clip' if clip_mic_enabled else 'phone'}",
        )
    )
    assert lip == out_lipsync, f"lipsync output path mismatch (got {lip})"
    assert mix == out_mix, f"mix output path mismatch (got {mix})"
    assert os.path.exists(lip), "lipsync output not written"
    assert os.path.exists(mix), "mix output not written"
    probe_lip = _probe(lip)
    probe_mix = _probe(mix)
    return lip, mix, probe_lip, probe_mix


def test_dual_outputs_phone_mic():
    """Phone-mic profile: both outputs exist with correct format/SR."""
    lip, mix, plip, pmix = _run_dual_output(clip_mic_enabled=False)

    # Lipsync = 16 kHz mono PCM WAV
    assert plip["sample_rate"] == 16000
    assert plip["channels"] == 1
    assert plip["codec_name"].startswith("pcm_"), f"got {plip['codec_name']}"
    # Mix = 48 kHz mono MP3 (Round 7: aligned to the 48 kHz pipeline so the
    # downstream remux no longer resamples 44.1 -> 48).
    assert pmix["sample_rate"] == 48000
    assert pmix["channels"] == 1
    assert pmix["codec_name"] in ("mp3", "mp3float"), f"got {pmix['codec_name']}"
    # Both ~3s
    assert 2.7 <= plip["duration_s"] <= 3.3
    assert 2.7 <= pmix["duration_s"] <= 3.3
    # 192 kbps MP3 vs PCM 16k mono: 192k * 3s ≈ 72 KB; PCM 16k mono * 16 bit
    # * 3s = 96 KB raw, but plus WAV header. Test: both files non-trivial.
    assert os.path.getsize(lip) > 1000
    assert os.path.getsize(mix) > 1000


def test_dual_outputs_clip_mic():
    """Clip-mic profile: same shape contract, different EQ inside."""
    lip, mix, plip, pmix = _run_dual_output(clip_mic_enabled=True)

    assert plip["sample_rate"] == 16000
    assert plip["channels"] == 1
    assert pmix["sample_rate"] == 48000
    assert pmix["channels"] == 1
    assert 2.7 <= plip["duration_s"] <= 3.3
    assert 2.7 <= pmix["duration_s"] <= 3.3


def test_failure_returns_input():
    """A bogus input path returns ``(input_path, input_path)`` so the
    caller can fall back to the raw TTS bytes without aborting.
    """
    bogus = "/tmp/this/file/does/not/exist/voice.mp3"
    out_lip = "/tmp/ppv_should_not_exist.lipsync.wav"
    out_mix = "/tmp/ppv_should_not_exist.mix.mp3"
    lip, mix = asyncio.run(
        post_process_voice(
            bogus, out_lip, out_mix,
            clip_mic_enabled=False,
            block_id="blk_bogus",
        )
    )
    assert lip == bogus, f"expected input fallback, got {lip}"
    assert mix == bogus, f"expected input fallback, got {mix}"
    # The output paths must NOT have been created (or if ffmpeg started
    # and crashed, they should be left empty / non-existent).
    assert not os.path.exists(out_lip) or os.path.getsize(out_lip) == 0
    assert not os.path.exists(out_mix) or os.path.getsize(out_mix) == 0
