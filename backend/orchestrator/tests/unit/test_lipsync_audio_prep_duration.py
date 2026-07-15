"""regression-2b: the lipsync-prepared audio must end when speech ends.

PR #82 padded BOTH edges of the prep'd WAV (``adelay=100|100`` head +
``apad`` tail). The bonded video is baked by the lipsync engine against
this padded audio, so the silent tail produced lip motion with no voice
after the speech ended (visible at every block boundary on the final mux).

regression-2b removes the tail ``apad`` and keeps only the head anchor.
These tests prove, end to end through ffmpeg:

  1. The prepared output duration equals the input duration plus the
     100ms head pad (±tolerance) — i.e. no tail pad is appended.
  2. There is leading silence (~100ms head pad) but NO comparable silent
     tail pad — the audio ends on speech.
  3. The duration-preservation assertion raises when a filter eats real
     speech (simulated by tightening the drift gate below the head pad).

These need ffmpeg/ffprobe on PATH; they skip otherwise. No DB fixtures.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from services import lipsync_audio_prep
from services.lipsync_audio_prep import prepare_lipsync_audio


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH"
)


def _synth_speech_wav(out_path: str, duration_s: float) -> None:
    """A continuous tone for the whole duration — no internal or trailing
    silence, so any silence in the output is pad introduced by the prep."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats", "-f", "lavfi", "-i",
        f"sine=frequency=220:duration={duration_s}",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"synth failed: {proc.stderr[-300:]}")


def _leading_silence_s(path: str) -> float:
    """Seconds of leading silence as reported by ffmpeg silencedetect."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", path,
            "-af", "silencedetect=noise=-50dB:d=0.03", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=60,
    )
    err = proc.stderr or ""
    # The first "silence_end" after a leading "silence_start: 0" is the
    # length of the head pad. If there is no leading silence, return 0.
    lead = 0.0
    for line in err.splitlines():
        if "silence_end" in line:
            try:
                lead = float(line.split("silence_end:")[1].split("|")[0].strip())
            except (IndexError, ValueError):
                lead = 0.0
            break
    return lead


class _FakeR2:
    """Minimal in-memory R2 matching the surface prepare_lipsync_audio uses:
    key_exists / upload_file / get_public_url.

    upload_file copies the uploaded WAV into a persistent directory because
    prepare_lipsync_audio writes its output inside a TemporaryDirectory that
    is deleted on return — the test needs the bytes to outlive that scope."""

    def __init__(self, persist_dir: str) -> None:
        self.objects: dict[str, str] = {}
        self._persist_dir = persist_dir

    async def key_exists(self, key: str) -> bool:
        return key in self.objects

    async def upload_file(self, local_path: str, key: str, *, content_type=None) -> None:
        dst = os.path.join(self._persist_dir, key.replace("/", "_"))
        shutil.copyfile(local_path, dst)
        self.objects[key] = dst

    def get_public_url(self, key: str) -> str:
        return f"https://r2.test/{key}"


@pytest.fixture
def fake_r2(tmp_path):
    persist = tmp_path / "r2"
    persist.mkdir()
    return _FakeR2(str(persist))


@pytest.fixture
def patched_download(monkeypatch, tmp_path):
    """Patch _download_source so prepare_lipsync_audio reads a local
    synthesized WAV instead of hitting the network. The source URL maps
    to a file we control via the closure."""
    src_wav = str(tmp_path / "source.wav")

    async def _fake_download(src_url: str, dst_path: str, *, timeout_s: float) -> None:
        shutil.copyfile(src_wav, dst_path)

    monkeypatch.setattr(lipsync_audio_prep, "_download_source", _fake_download)
    return src_wav


async def test_output_duration_is_input_plus_head_pad_only(
    fake_r2, patched_download, tmp_path
):
    """The prepared WAV is exactly input + 100ms head pad — no tail pad."""
    in_dur = 3.0
    _synth_speech_wav(patched_download, in_dur)

    url = await prepare_lipsync_audio(
        "https://cdn/tts/blk.wav", "rnd_test", "blk_0", r2=fake_r2,
    )

    key = url.split("https://r2.test/")[1]
    out_path = fake_r2.objects[key]
    out_dur = lipsync_audio_prep._probe_duration_s(out_path)

    expected = in_dur + lipsync_audio_prep._HEAD_PAD_S
    assert abs(out_dur - expected) < 0.030, (
        f"output {out_dur:.3f}s should be input+head_pad {expected:.3f}s "
        f"(no tail pad); drift {abs(out_dur - expected) * 1000:.0f}ms"
    )


async def test_no_trailing_silence_pad(fake_r2, patched_download, tmp_path):
    """There is a ~100ms head pad but the audio ends on speech: total
    duration only accounts for ONE pad's worth of silence, proving the
    old tail apad is gone."""
    in_dur = 2.5
    _synth_speech_wav(patched_download, in_dur)

    url = await prepare_lipsync_audio(
        "https://cdn/tts/blk.wav", "rnd_test", "blk_1", r2=fake_r2,
    )
    out_path = fake_r2.objects[url.split("https://r2.test/")[1]]

    lead = _leading_silence_s(out_path)
    out_dur = lipsync_audio_prep._probe_duration_s(out_path)

    # Head pad is present (the engine anchor we deliberately keep).
    assert lead >= lipsync_audio_prep._HEAD_PAD_S - 0.030, (
        f"expected ~{lipsync_audio_prep._HEAD_PAD_S}s leading silence, got {lead:.3f}s"
    )
    # Total silence beyond speech ≈ head pad only. If a tail pad were still
    # present, out_dur would be in_dur + 2*head_pad.
    total_pad = out_dur - in_dur
    assert total_pad < lipsync_audio_prep._HEAD_PAD_S + 0.030, (
        f"total padding {total_pad * 1000:.0f}ms exceeds a single head pad — "
        f"a tail pad is still being appended"
    )


async def test_duration_drift_assertion_raises_when_filter_eats_speech(
    fake_r2, patched_download, tmp_path, monkeypatch
):
    """If a filter changes the length beyond (input + head pad) — the exact
    failure mode that produces lips-without-voice — the prep raises rather
    than shipping the mismatched audio. We simulate it by making the FINAL
    output probe report a duration far from the expected post-head-pad
    length, leaving the input probe accurate."""
    in_dur = 2.0
    _synth_speech_wav(patched_download, in_dur)

    real_probe = lipsync_audio_prep._probe_duration_s
    calls = {"n": 0}

    def _probe_side_effect(path: str) -> float:
        calls["n"] += 1
        # First probe is the source duration (used for timeouts + expected
        # baseline); return the real value. The final probe is the output
        # duration check — return a value 1s short to simulate eaten speech.
        if calls["n"] == 1:
            return real_probe(path)
        return in_dur - 1.0

    monkeypatch.setattr(
        lipsync_audio_prep, "_probe_duration_s", _probe_side_effect
    )

    with pytest.raises(RuntimeError, match="duration drift"):
        await prepare_lipsync_audio(
            "https://cdn/tts/blk.wav", "rnd_test", "blk_2", r2=fake_r2,
        )
