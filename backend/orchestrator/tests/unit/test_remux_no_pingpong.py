"""Regression guard for the "lady walks backwards" bug.

PR #93 fixed the per-block reverse-walk path. This suite covers the
SECOND, independent offender: the timeline-level extension inside
``tasks.cast_render._post_compose_audio_remux``, which used to call
``services.block_normalize.build_pingpong_video_filter`` (and a
micro-pad / hold-frame / re-bake ladder) whenever the composed clip
undershot the expected duration. Sub-frame rounding across many
concatenated blocks reliably tripped that path, so even a perfectly-baked
timeline could play its tail in reverse.

Phase 1 deletes all of that. Under the overshoot+trim strategy every
block is baked LONGER than its slot and head-trimmed to exactly slot
length, so the composed timeline duration must already equal the sum of
slot durations. The contract we ship and lock down here:

  * ``build_pingpong_video_filter`` no longer exists in
    ``services.block_normalize`` and is referenced nowhere in
    ``cast_render.py``.
  * The remux passes ``[0:v]`` through UNTOUCHED — no pad / hold / reverse
    / re-bake. When the composed video already matches the expected
    duration (within a single-frame rounding margin) the remux only
    rebuilds the audio mix and re-uploads.
  * A material undershoot is a real defect: the remux raises
    ``RenderExtensionFailed`` instead of papering over it.

Both a behavioural test (run the real function across the in-tolerance
and out-of-tolerance buckets with ffmpeg) and static-source guards are
included.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; remux tests need both",
)


_THIS_DIR = Path(__file__).resolve().parent
_CAST_RENDER = _THIS_DIR.parent.parent / "tasks" / "cast_render.py"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _synth_clip(out_path: str, *, duration_s: float, fps: int = 30) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=size=480x848:duration={duration_s}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration_s}",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")


def _probe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return 0.0


class _FakeR2:
    """Minimal R2 stand-in. download_file copies a prepared compose clip;
    upload_file records the bytes uploaded so the test can probe the
    final duration. No network."""

    def __init__(self, compose_src: str):
        self._compose_src = compose_src
        self.uploaded_path: str | None = None

    async def download_file(self, key: str, dest: str) -> None:
        shutil.copyfile(self._compose_src, dest)

    async def upload_file(self, local_path: str, key: str, content_type: str = "") -> None:
        # Persist a copy so the temp dir cleanup in the function under test
        # doesn't delete it before we probe.
        fd, persisted = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        shutil.copyfile(local_path, persisted)
        self.uploaded_path = persisted

    async def upload_bytes(self, *_a, **_k) -> None:  # pragma: no cover
        return None

    def get_public_url(self, key: str) -> str:  # pragma: no cover
        return f"https://r2.test/{key}"


def _timeline_for(expected_duration: float) -> dict:
    """One audio element on an audio track + one element carrying the
    expected end time ``e`` so _expected_timeline_duration_s returns it."""
    return {
        "compositionWidth": 480,
        "compositionHeight": 848,
        "fps": 30,
        "tracks": [
            {
                "type": "audio",
                "elements": [
                    {"s": 0.0, "e": expected_duration,
                     "props": {"src": "https://audio.test/a0.wav"}},
                ],
            },
        ],
    }


def _fake_http_client(audio_path: str):
    """Return an object usable as ``async with httpx.AsyncClient(...) as c``
    whose ``.get`` yields the bytes of a local audio file."""
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    class _Resp:
        content = audio_bytes

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, *_a, **_k):
            return _Resp()

    return _Client


def _synth_audio(audio_src: str, *, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=330:duration={duration_s}",
         "-ar", "48000", "-ac", "2", audio_src],
        capture_output=True, text=True, timeout=60,
    )


def test_remux_in_tolerance_passes_video_through_untouched():
    """Composed video already matches expected (within the single-frame
    rounding margin) → remux rebuilds audio only, passes [0:v] through,
    and re-uploads a clip that still lands on the expected duration. No
    pad / hold / reverse / ping-pong."""
    from tasks import cast_render

    expected_s = 3.00
    compose_s = 3.00  # exact match, well inside _TL_MICRO_PAD_MAX_S

    with tempfile.TemporaryDirectory() as tmp:
        compose_src = os.path.join(tmp, "compose.mp4")
        audio_src = os.path.join(tmp, "a0.wav")
        _synth_clip(compose_src, duration_s=compose_s)
        _synth_audio(audio_src, duration_s=expected_s)

        r2 = _FakeR2(compose_src)
        timeline = _timeline_for(expected_s)

        with patch("httpx.AsyncClient", _fake_http_client(audio_src)):
            _run(
                cast_render._post_compose_audio_remux(
                    r2=r2,
                    output_key="renders/test/final.mp4",
                    timeline=timeline,
                    render_id="rnd_f2c74e4e1106",
                )
            )

        assert r2.uploaded_path is not None, "remux never uploaded a result"
        try:
            final_s = _probe_duration(r2.uploaded_path)
        finally:
            os.unlink(r2.uploaded_path)
        assert abs(final_s - expected_s) <= 0.1, (
            f"in-tolerance remux should preserve expected={expected_s}s; "
            f"got {final_s:.3f}s"
        )


def test_remux_material_undershoot_fails_instead_of_padding():
    """A materially short composed timeline (> _TL_MICRO_PAD_MAX_S) is a
    real defect under overshoot+trim. The remux must RAISE
    ``RenderExtensionFailed`` rather than pad / hold / reverse the tail."""
    from tasks import cast_render

    expected_s = 3.63
    compose_s = 1.05  # short by 2.58s — way outside the rounding margin

    with tempfile.TemporaryDirectory() as tmp:
        compose_src = os.path.join(tmp, "compose.mp4")
        audio_src = os.path.join(tmp, "a0.wav")
        _synth_clip(compose_src, duration_s=compose_s)
        _synth_audio(audio_src, duration_s=expected_s)

        r2 = _FakeR2(compose_src)
        timeline = _timeline_for(expected_s)

        with patch("httpx.AsyncClient", _fake_http_client(audio_src)):
            with pytest.raises(cast_render.RenderExtensionFailed):
                _run(
                    cast_render._post_compose_audio_remux(
                        r2=r2,
                        output_key="renders/test/final.mp4",
                        timeline=timeline,
                        render_id="rnd_f2c74e4e1106",
                    )
                )

        # Refused to ship a padded clip.
        assert r2.uploaded_path is None, (
            "material undershoot must fail the render, not upload a "
            "padded/held/reversed clip"
        )


def test_block_normalize_has_no_pingpong_symbol():
    """Static guard: the ping-pong builder is deleted from block_normalize
    (the per-block reverse-walk source)."""
    import services.block_normalize as bn

    assert not hasattr(bn, "build_pingpong_video_filter"), (
        "build_pingpong_video_filter must be deleted from block_normalize "
        "— it produced the 'lady walks backwards' artefact"
    )


def test_cast_render_source_has_no_pingpong_in_remux():
    """Static guard: the ping-pong import/call must not reappear anywhere
    in cast_render.py."""
    src = _CAST_RENDER.read_text()
    assert "build_pingpong_video_filter" not in src, (
        "build_pingpong_video_filter must not be referenced in "
        "cast_render.py — the timeline-level reverse is the "
        "'lady walks backwards' bug."
    )
