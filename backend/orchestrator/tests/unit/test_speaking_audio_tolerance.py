"""Unit tests for Render_Quality_Duration_Validation.md §2.1 — speaking
blocks compare to TTS audio (not slot), plus the planning-layer
slot↔audio reconciliation.

Covers ``tasks.cast_render``:
  * ``_reconcile_slot_vs_audio`` / ``_resize_slot_to_audio`` — pure timeline
    math, no ffmpeg.
  * ``_enforce_speaking_tolerance`` — the audio-fix regression test: a bake
    within tolerance of the AUDIO is accepted even when the slot wildly
    differs. Synthesizes clips via ffmpeg lavfi; skipped when ffmpeg/ffprobe
    are absent.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest


# ``sentry_sdk`` is a real installed dependency; importing ``tasks.cast_render``
# pulls in the Celery app (``tasks/__init__.py``), which needs the genuine
# ``sentry_sdk.integrations.celery`` module. Do NOT stub it — a SimpleNamespace
# stub shadows the real package and breaks that import. The gate's
# ``capture_exception`` calls are harmless no-ops without a configured DSN.
from tasks.cast_render import (  # noqa: E402
    _reconcile_slot_vs_audio,
    _resize_slot_to_audio,
    _enforce_speaking_tolerance,
    _SLOT_AUDIO_MISMATCH_TOLERANCE,
)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _timeline_with_slot(block_id: str, s: float, e: float) -> dict:
    return {
        "tracks": [
            {
                "type": "video",
                "elements": [
                    {
                        "id": f"v1_{block_id}",
                        "s": s,
                        "e": e,
                        "metadata": {"block_id": block_id},
                    }
                ],
            }
        ]
    }


# ── Pure planning math: reconcile + resize ──────────────────────────────


def test_resize_slot_to_audio_mutates_end():
    tl = _timeline_with_slot("blk1", s=2.0, e=11.4)  # 9.4s slot
    ok = _resize_slot_to_audio(tl, "blk1", 3.2)
    assert ok is True
    el = tl["tracks"][0]["elements"][0]
    assert el["s"] == 2.0
    assert abs(el["e"] - 5.2) < 1e-6  # s + audio


def test_reconcile_within_tolerance_keeps_slot():
    # slot 9.40, audio 9.45 → drift ~0.5% < 2% → unchanged, no failure.
    tl = _timeline_with_slot("blk1", s=0.0, e=9.40)
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl, block_id="blk1", audio_duration_s=9.45, slot_duration_s=9.40,
    )
    assert mismatch is None
    assert abs(new_slot - 9.40) < 1e-6
    # Slot element untouched.
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 9.40) < 1e-6


def test_reconcile_never_shrinks_slot_when_audio_under_slot():
    # REGRESSION GUARD (the rnd_124ec1f95740 / blk_afc2418b63b2 bug): the
    # user slot is 10.03s but the TTS audio is only 3.18s. The OLD code
    # shrank the slot to the audio (producing trailing black). The user rule
    # is non-negotiable: NEVER cut the render to the user slot. The slot must
    # be HELD at 10.03s (the video is extended downstream to fill it), no
    # failure reason is returned, and the slot element is NOT mutated.
    tl = _timeline_with_slot("blk_afc2418b63b2", s=0.0, e=10.03)
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl,
        block_id="blk_afc2418b63b2",
        audio_duration_s=3.18,
        slot_duration_s=10.03,
        fixed_length=False,
    )
    assert mismatch is None
    # Slot returned UNCHANGED — never shrunk to the audio.
    assert abs(new_slot - 10.03) < 1e-6
    # Timeline element NOT mutated (e stays at the user value).
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 10.03) < 1e-6


def test_reconcile_never_shrinks_even_for_extreme_undershoot():
    # slot wildly longer than audio (slot > 2× audio): still NEVER shrink.
    # The slot_audio_undershoot_extreme warning is observability only — the
    # slot is held and the function returns it unchanged with no failure.
    tl = _timeline_with_slot("blk_extreme", s=1.0, e=21.0)  # 20s slot
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl,
        block_id="blk_extreme",
        audio_duration_s=3.0,  # slot is 6.7× the audio
        slot_duration_s=20.0,
        fixed_length=False,
    )
    assert mismatch is None
    assert abs(new_slot - 20.0) < 1e-6
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 21.0) < 1e-6


def test_reconcile_never_shrinks_even_when_fixed_length():
    # A fixed-length flag must not turn an audio-under-slot case into a
    # slot_audio_mismatch failure (that path is only for audio OVERRUN). The
    # slot is held; the video is extended downstream. Never shrink, never fail.
    tl = _timeline_with_slot("blk_fixed", s=0.0, e=10.03)
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl,
        block_id="blk_fixed",
        audio_duration_s=3.18,
        slot_duration_s=10.03,
        fixed_length=True,
    )
    assert mismatch is None
    assert abs(new_slot - 10.03) < 1e-6
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 10.03) < 1e-6


def test_reconcile_extends_slot_when_audio_overruns_short_slot():
    # The OTHER direction: audio OVERRUNS a too-short slot (slot < audio).
    # Extending the slot UP to the audio is itself an allowed extension (the
    # slot grows, it is never cut), so a resizable slot is mutated to audio.
    tl = _timeline_with_slot("blk_overrun", s=0.0, e=3.0)
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl,
        block_id="blk_overrun",
        audio_duration_s=9.4,
        slot_duration_s=3.0,
        fixed_length=False,
    )
    assert mismatch is None
    assert abs(new_slot - 9.4) < 1e-6
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 9.4) < 1e-6


def test_reconcile_fails_fixed_length_slot_on_audio_overrun():
    # A fixed-length slot that the audio OVERRUNS cannot be resized →
    # slot_audio_mismatch, slot kept.
    tl = _timeline_with_slot("blk_fixed_overrun", s=0.0, e=3.0)
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl,
        block_id="blk_fixed_overrun",
        audio_duration_s=9.4,
        slot_duration_s=3.0,
        fixed_length=True,
    )
    assert mismatch == "slot_audio_mismatch"
    assert abs(new_slot - 3.0) < 1e-6
    # Fixed slot must NOT be mutated.
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 3.0) < 1e-6


def test_reconcile_autoresize_disabled_fails_instead_of_resizing(monkeypatch):
    # §2.2: SLOT_AUDIO_AUTORESIZE_ENABLED=false makes even a resizable slot
    # surface as slot_audio_mismatch rather than being silently corrected —
    # but only on the audio-OVERRUN direction (slot < audio).
    import tasks.cast_render as cr

    monkeypatch.setattr(cr, "_SLOT_AUDIO_AUTORESIZE_ENABLED", False)
    tl = _timeline_with_slot("blk_noresize", s=0.0, e=3.0)
    new_slot, mismatch = cr._reconcile_slot_vs_audio(
        tl,
        block_id="blk_noresize",
        audio_duration_s=9.4,
        slot_duration_s=3.0,
        fixed_length=False,
    )
    assert mismatch == "slot_audio_mismatch"
    assert abs(new_slot - 3.0) < 1e-6
    # Slot must NOT be mutated when auto-resize is disabled.
    assert abs(tl["tracks"][0]["elements"][0]["e"] - 3.0) < 1e-6


def test_autoresize_flag_default_true():
    import tasks.cast_render as cr

    assert cr._SLOT_AUDIO_AUTORESIZE_ENABLED is True


def test_reconcile_unknown_duration_is_noop():
    tl = _timeline_with_slot("blk1", s=0.0, e=5.0)
    new_slot, mismatch = _reconcile_slot_vs_audio(
        tl, block_id="blk1", audio_duration_s=0.0, slot_duration_s=5.0,
    )
    assert mismatch is None
    assert abs(new_slot - 5.0) < 1e-6


def test_tolerance_constant_default():
    # Default 2% unless overridden by SLOT_AUDIO_MISMATCH_TOLERANCE.
    assert _SLOT_AUDIO_MISMATCH_TOLERANCE > 0


# ── Speaking-tolerance gate: compare to AUDIO, not slot ─────────────────

pytestmark_ffmpeg = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; speaking-tolerance synth tests need both",
)


def _synth_clip(path: str, *, duration_s: float) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-f", "lavfi", "-i",
            f"testsrc2=size=320x568:duration={duration_s}:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
            "-t", f"{duration_s}", path,
        ],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"synth ffmpeg failed: {proc.stderr[-400:]}")
    with open(path, "rb") as fh:
        return fh.read()


@pytestmark_ffmpeg
def test_speaking_bake_accepted_against_audio_despite_wrong_slot():
    """The §2.1 audio-fix regression test.

    A 3.2s speaking bake whose AUDIO is 3.2s is ACCEPTED even though the
    timeline slot is 9.433s (the rnd_7923dde6c01f mismatch). Comparing to
    the slot would have wrongly rejected it; comparing to the audio passes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "bake.mp4")
        clip_bytes = _synth_clip(clip, duration_s=3.2)
        # Slot wildly differs from the audio; audio_duration_s drives the band.
        tl = _timeline_with_slot("blk", s=0.0, e=9.433)
        out = _run(
            _enforce_speaking_tolerance(
                clip_bytes,
                timeline=tl,
                block_id="blk",
                render_id="r",
                fallback_duration_s=9.433,
                audio_duration_s=3.2,
            )
        )
        # Within band → bytes returned unchanged (no trim, no raise).
        assert out == clip_bytes


@pytestmark_ffmpeg
def test_speaking_bake_within_absolute_trailing_silence_allowance():
    """REGRESSION GUARD (cst_294eeaf04af2 / rnd_e116aea4902c blocks
    blk_8f8d99a88f8e, blk_1a61c93a5731): fal-ai/sync-lipsync/v3 declines to
    render ~120-170ms of trailing near-silence off the end of the audio,
    a roughly fixed absolute amount regardless of clip length. Confirmed via
    ffmpeg silencedetect that this trim lands inside (or within ~30ms of) the
    source audio's own trailing-silence region — no speech lost. A pure -2%
    floor wrongly failed these short (~5s) blocks; the absolute allowance
    must let a ~160ms shortfall on a short clip pass.
    """
    with tempfile.TemporaryDirectory() as tmp:
        audio_s = 4.839
        bake_s = audio_s - 0.159  # matches the observed real-world trim
        clip = os.path.join(tmp, "bake.mp4")
        clip_bytes = _synth_clip(clip, duration_s=bake_s)
        tl = _timeline_with_slot("blk", s=0.0, e=audio_s)
        out = _run(
            _enforce_speaking_tolerance(
                clip_bytes,
                timeline=tl,
                block_id="blk",
                render_id="r",
                fallback_duration_s=audio_s,
                audio_duration_s=audio_s,
            )
        )
        # -159ms is inside [-max(2%, 0.25s), +5%] for a 4.839s reference
        # (2% floor would be 4.742s — too tight; the 0.25s absolute
        # allowance floors it at 4.589s instead) → accepted untouched.
        assert out == clip_bytes


@pytestmark_ffmpeg
def test_speaking_bake_out_of_band_against_audio_raises():
    """A bake materially shorter than the AUDIO is still a defect."""
    from tasks.cast_render import SpeakingBlockOutOfTolerance

    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "bake.mp4")
        clip_bytes = _synth_clip(clip, duration_s=2.0)
        tl = _timeline_with_slot("blk", s=0.0, e=2.0)
        with pytest.raises(SpeakingBlockOutOfTolerance):
            _run(
                _enforce_speaking_tolerance(
                    clip_bytes,
                    timeline=tl,
                    block_id="blk",
                    render_id="r",
                    fallback_duration_s=5.0,
                    audio_duration_s=5.0,  # bake is 2.0s → 60% short of audio
                )
            )
