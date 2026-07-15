"""Unit tests for services.block_extension.trim_block_to_slot.

Phase 1 replaced the pad/reverse/re-bake extension ladder with a
trim-only conform: motion clips are baked LONGER than their slot
(overshoot) and head-trimmed down to exactly slot length. There is no
ping-pong, no short-tail-reverse, no frame-clone, no I2V re-bake. The
only retime is the bounded Phase 1.3 micro-slowdown for a small motion
shortfall.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

import services.block_extension as block_extension
from services.block_extension import (
    extend_video_bytes_to_duration,
    probe_frame_counted_duration_s,
    trim_block_to_slot,
)
from services.block_normalize import _probe_streams


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _count_frames(path: str) -> int:
    """Return the exact decoded video frame count of ``path`` via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True, timeout=120,
    )
    return int((out.stdout or "0").strip() or 0)


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; block_extension tests need both",
)


def _synth_clip(
    *,
    out_path: str,
    width: int,
    height: int,
    duration_s: float,
    fps: int,
    with_audio: bool,
) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:duration={duration_s}:rate={fps}",
    ]
    if with_audio:
        cmd.extend([
            "-f", "lavfi", "-i",
            f"sine=frequency=440:duration={duration_s}",
        ])
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"])
    cmd.extend([
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-t", f"{duration_s}",
        out_path,
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"synth ffmpeg failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )


def _run_trim(bake_bytes: bytes, *, slot_s: float, is_motion: bool = False,
              w: int = 480, h: int = 848, fps: int = 30) -> bytes:
    return asyncio.new_event_loop().run_until_complete(
        trim_block_to_slot(
            bake_bytes=bake_bytes,
            slot_s=slot_s,
            target_width=w,
            target_height=h,
            target_fps=fps,
            block_id="blk_test",
            render_id="rnd_test",
            is_motion=is_motion,
        )
    )


def test_head_trim_overshoot_lands_on_slot():
    """Overshot bake=5.75s, slot=5.0s → head-trim lands ≈ 5.0s."""
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=5.75, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=5.0, is_motion=True)
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 4.95 <= probe["duration_s"] <= 5.05, (
            f"head-trim should land near slot=5.0s; got {probe['duration_s']:.3f}s"
        )


def test_exact_length_passthrough_trims_clean():
    """Bake already == slot → still lands exactly on slot."""
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=4.0, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=4.0, is_motion=True)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 3.95 <= probe["duration_s"] <= 4.05


def test_motion_micro_slowdown_small_shortfall():
    """Motion bake=4.85s, slot=5.0s (3% short) → micro-slowdown lands ≈ 5.0s."""
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=4.85, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=5.0, is_motion=True)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 4.95 <= probe["duration_s"] <= 5.05, (
            f"micro-slowdown should land near slot=5.0s; got {probe['duration_s']:.3f}s"
        )


def test_speaking_audio_under_slot_extends_to_slot():
    """The rnd_124ec1f95740 / blk_afc2418b63b2 bug.

    A SPEAKING bake of 3.18s for a user-defined slot of 10.03s must be
    EXTENDED to fill the slot (never shrunk). Producing a 3.18s clip left
    6.85s of trailing black after compose. The output must land on the full
    slot length.
    """
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=3.18, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        # is_motion=False → speaking path → extend to slot.
        out_bytes = _run_trim(bake_bytes, slot_s=10.03, is_motion=False)
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 9.93 <= probe["duration_s"] <= 10.13, (
            f"speaking clip must be EXTENDED to slot=10.03s, not left at the "
            f"3.18s audio length; got {probe['duration_s']:.3f}s"
        )


def test_speaking_small_shortfall_extends_to_slot():
    """A speaking bake 3% short of its slot is extended (micro-slowdown tier)."""
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=4.85, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=5.0, is_motion=False)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 4.93 <= probe["duration_s"] <= 5.07, (
            f"speaking clip must be extended to slot=5.0s; got "
            f"{probe['duration_s']:.3f}s"
        )


def test_large_shortfall_not_padded():
    """Bake=1.05s, slot=3.63s (33% short) — overshoot contract violated.

    We must NOT pad/reverse/stretch it up. The function returns the
    (untrimmed) bake so the Phase 3 validation gate can reject it; the
    output therefore stays close to the bake length, NOT the slot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=1080, height=1920,
                    duration_s=1.05, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=3.63, is_motion=True,
                              w=1080, h=1920)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert probe["duration_s"] < 1.5, (
            f"large shortfall must not be padded to slot; got "
            f"{probe['duration_s']:.3f}s (expected ~1.05s passthrough)"
        )


def test_freeze_frame_extension_actually_extends_stream():
    """PR-H: a freeze-frame extension must extend the FRAME STREAM, not just
    the container header.

    Synthesize a 2s speaking clip, extend to a 3s slot, and assert the
    frame-COUNTED ffprobe duration (``-count_frames``) is ~3s. Container
    metadata could lie (the original bug); the frame count cannot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=2.0, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        # is_motion=False → speaking path → extend to slot via freeze/loop.
        out_bytes = _run_trim(bake_bytes, slot_s=3.0, is_motion=False)
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=30)
        assert 2.95 <= frame_dur <= 3.05, (
            f"freeze-frame extension must extend the actual frame stream to "
            f"slot=3.0s; frame-counted duration was {frame_dur:.3f}s"
        )


def test_head_trim_undershoot_is_filled_to_slot():
    """PR-H2: head-trim of a non-30fps-style overshoot must land on exact slot frames.

    A 12.07s bake head-trimmed to a 9.3s slot can land 1–4 frames short of the
    slot's true frame count due to frame-boundary rounding, which the
    frame-counted Phase 3 validator rejects as a duration undershoot. After the
    PR-H2 top-up the output must carry exactly round(9.3 * 30) = 279 frames.
    """
    slot_s = 9.3
    fps = 30
    expected_frames = round(slot_s * fps)  # 279
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=1080, height=1920,
                    duration_s=12.07, fps=fps, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=slot_s, is_motion=True,
                              w=1080, h=1920, fps=fps)
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        actual_frames = _count_frames(out_path)
        assert actual_frames == expected_frames, (
            f"head-trim must pad to exactly {expected_frames} frames "
            f"(round(9.3 * 30)); got {actual_frames}"
        )


def test_extend_video_bytes_to_duration_uses_frame_counted_probe(monkeypatch):
    """PR-H3: the early-return probe must be frame-counted, not container.

    Head-trimmed motion clips carry a container header inflated by ffmpeg's
    ``-t`` flag (says 5.0s) while the real frame stream stops short. The
    early-return guard previously used ``_probe_duration_s`` (container
    metadata), which read 5.0s ≥ target and silently returned the unchanged
    short clip — masking every motion-block head-trim undershoot. With a
    frame-counted probe the guard sees the true sub-slot length and the extend
    ladder runs, landing the output within a frame of the 5.0s slot.

    The micro-slowdown tier is disabled here so the small shortfall routes to
    the frame-exact tail-freeze / still-image rebuild path; the test isolates
    the entry-point probe, not which extension tier fills the slot.
    """
    monkeypatch.setattr(
        "services.block_extension.MOTION_MICRO_SETPTS_ENABLED", False
    )
    fps = 30
    target_s = 5.0
    with tempfile.TemporaryDirectory() as tmp:
        # Build a clip whose CONTAINER says 5.0s but whose VIDEO frame stream
        # is short of the slot — the exact lie a head-trimmed motion clip
        # carries (ffmpeg's -t flag / a longer audio stream inflates
        # format=duration while the real frame stream stops short).
        # format=duration reports the max stream length, so a 5.0s audio track
        # over a 4.85s video track yields a 5.0s container atop a 4.85s frame
        # stream — container ≥ slot (the container-probe early-return would
        # fire) but the frame stream is below the slot floor (the frame-counted
        # probe lets the ladder run). The video runs to 4.85s so the
        # still-image fallback's end-seek lands on a real video frame, not the
        # audio-only tail.
        video_s = 4.85
        inflated_path = os.path.join(tmp, "inflated.mp4")
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                f"testsrc2=size=480x848:duration={video_s}:rate={fps}",
                "-f", "lavfi", "-i",
                f"sine=frequency=440:duration={target_s}",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                inflated_path,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"inflate ffmpeg failed (rc={proc.returncode}): "
                f"{proc.stderr[-500:]}"
            )
        with open(inflated_path, "rb") as f:
            inflated_bytes = f.read()

        # Sanity: the frame stream is genuinely short (< target by > 1 frame)
        # while the container metadata claims it is at/above target. This is
        # the divergence that makes the container-probe early-return no-op.
        in_frame_dur = probe_frame_counted_duration_s(inflated_path, target_fps=fps)
        assert in_frame_dur < target_s - (1.0 / fps), (
            f"test setup: input frame stream must be short of {target_s}s; "
            f"got {in_frame_dur:.3f}s"
        )

        out_bytes = extend_video_bytes_to_duration(
            video_bytes=inflated_bytes,
            target_s=target_s,
            target_fps=fps,
            block_id="blk_test",
            render_id="rnd_test",
        )
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=fps)
        assert frame_dur >= 4.97, (
            f"frame-counted early-return must let the extend ladder run; the "
            f"output should reach within a frame of slot={target_s}s, but got "
            f"{frame_dur:.3f}s (the container-probe bug returns the short original)"
        )


def test_av_reconcile_never_shrinks_video_below_slot():
    """PR-H: when audio is longer than video in a user slot, the video is
    EXTENDED to the audio length — the slot is never shrunk.

    Build a clip whose video is 2s and request extension to a 3s audio/slot
    length. The reconcile helper must produce a video stream of ~3s, NOT trim
    down to 2s.
    """
    with tempfile.TemporaryDirectory() as tmp:
        vid_path = os.path.join(tmp, "v.mp4")
        _synth_clip(out_path=vid_path, width=480, height=848,
                    duration_s=2.0, fps=30, with_audio=True)
        with open(vid_path, "rb") as f:
            video_bytes = f.read()

        out_bytes = extend_video_bytes_to_duration(
            video_bytes=video_bytes,
            target_s=3.0,
            target_fps=30,
            block_id="blk_test",
            render_id="rnd_test",
        )
        assert out_bytes and out_bytes != video_bytes, (
            "reconcile must extend the video, not return the short original"
        )
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=30)
        assert frame_dur >= 2.95, (
            f"audio>video reconcile must extend video to slot=3.0s, never "
            f"shrink to 2.0s; frame-counted duration was {frame_dur:.3f}s"
        )


# ─── PR-D: tail-freeze elimination ─────────────────────────────────────────


def _synth_clip_with_trailing_silence(
    *, out_path: str, width: int, height: int, speech_s: float,
    silence_s: float, fps: int,
) -> None:
    """Synthesize a clip: ``speech_s`` of tone then ``silence_s`` of silence.

    The video runs the full ``speech_s + silence_s`` so the audio's trailing
    silence is genuinely *trailing padding* relative to a shorter-than-slot
    video — the exact shape the audio-trim tier targets.
    """
    total = speech_s + silence_s
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:duration={total}:rate={fps}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={speech_s}",
        "-f", "lavfi", "-i",
        f"anullsrc=r=48000:cl=stereo:duration={silence_s}",
        "-filter_complex",
        "[1:a][2:a]concat=n=2:v=0:a=1[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-t", f"{total}",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"synth-silence ffmpeg failed (rc={proc.returncode}): "
            f"{proc.stderr[-500:]}"
        )


def test_aggressive_micro_slowdown_for_short_gap(monkeypatch):
    """PR-D: a speaking bake ~8% short of a short slot uses the 10% cap.

    bake=4.6s, slot=5.0s → gap=0.4s (< 0.6s aggressive window), shortfall=8%.
    The old 5% cap would have skipped micro-slowdown and frozen the tail; with
    the aggressive cap the clip is retimed up to slot (no freeze, no reverse).
    """
    # Disable the lower tiers so a failure to take micro-slowdown is visible as
    # a different code path rather than silently filled by the reverse-loop.
    monkeypatch.setattr(block_extension, "TAIL_REVERSE_LOOP_ENABLED", False)
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=4.6, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=5.0, is_motion=False)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=30)
        assert 4.93 <= frame_dur <= 5.07, (
            f"8%% short within the 0.6s window must micro-slowdown to slot=5.0s; "
            f"got {frame_dur:.3f}s"
        )


def test_tail_reverse_loop_fills_slot_with_motion():
    """PR-D: a gap beyond the micro-slowdown cap is filled by the reverse-loop.

    bake=3.0s, slot=4.0s → gap=1.0s (> 10% shortfall, ≤ 1.5s reverse cap, no
    trailing silence). The reverse-tail bounce must fill the slot to length
    using real frames (no freeze, no black). Frame-counted duration ≈ 4.0s.
    """
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=3.0, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=4.0, is_motion=False)
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=30)
        assert 3.95 <= frame_dur <= 4.05, (
            f"reverse-loop must extend the frame stream to slot=4.0s; got "
            f"{frame_dur:.3f}s"
        )


def test_freeze_fallback_when_reverse_loop_disabled(monkeypatch):
    """PR-D: with the reverse-loop disabled, the freeze fallback still fills slot.

    Guards that disabling TAIL_REVERSE_LOOP_ENABLED degrades gracefully to the
    (still frame-exact) freeze path rather than leaving a short clip.
    """
    monkeypatch.setattr(block_extension, "TAIL_REVERSE_LOOP_ENABLED", False)
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=3.0, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=4.0, is_motion=False)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=30)
        assert 3.95 <= frame_dur <= 4.05, (
            f"freeze fallback must still fill slot=4.0s; got {frame_dur:.3f}s"
        )


def test_audio_trim_trailing_silence_lands_on_slot(monkeypatch):
    """PR-D: a near-slot clip with trailing audio silence trims the silence.

    Video = speech 4.85s + 0.4s padding (5.25s), but we request a slot of 4.9s
    where the gap (0.05s? no) — we route via the audio-trim tier: build a clip
    where video is 4.75s and slot is 5.0s (gap 0.25s ≤ 0.3s tolerance) and the
    audio has ≥0.25s trailing silence. The tier trims the trailing silence and
    lands on slot WITHOUT cutting speech. Output ≈ slot length.
    """
    # Force the micro-slowdown tier off so the audio-trim tier is exercised
    # (otherwise a 5% gap would be absorbed by micro-slowdown first).
    monkeypatch.setattr(block_extension, "MOTION_MICRO_SETPTS_ENABLED", False)
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        # video+audio total 4.75s: 4.5s tone + 0.25s trailing silence.
        _synth_clip_with_trailing_silence(
            out_path=bake_path, width=480, height=848,
            speech_s=4.5, silence_s=0.25, fps=30,
        )
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=5.0, is_motion=False)
        assert out_bytes
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        probe = _probe_streams(out_path)
        assert 4.93 <= probe["duration_s"] <= 5.07, (
            f"audio-trim tier must land on slot=5.0s; got "
            f"{probe['duration_s']:.3f}s"
        )


def test_large_gap_loops_not_reverse(monkeypatch):
    """PR-D: a gap larger than the reverse cap falls through to the full loop.

    bake=2.0s, slot=5.0s → gap=3.0s (> 1.5s reverse cap). Must still fill the
    slot (via the whole-clip loop), not freeze.
    """
    with tempfile.TemporaryDirectory() as tmp:
        bake_path = os.path.join(tmp, "bake.mp4")
        _synth_clip(out_path=bake_path, width=480, height=848,
                    duration_s=2.0, fps=30, with_audio=True)
        with open(bake_path, "rb") as f:
            bake_bytes = f.read()

        out_bytes = _run_trim(bake_bytes, slot_s=5.0, is_motion=False)
        out_path = os.path.join(tmp, "out.mp4")
        with open(out_path, "wb") as f:
            f.write(out_bytes)
        frame_dur = probe_frame_counted_duration_s(out_path, target_fps=30)
        assert 4.95 <= frame_dur <= 5.05, (
            f"large gap must loop to fill slot=5.0s; got {frame_dur:.3f}s"
        )
