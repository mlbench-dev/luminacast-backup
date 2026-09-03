"""Bake-to-slot conform: trim a (deliberately over-long) bake down to slot.

Strategy (locked, user-mandated — Phase 1):

    "never, do not cut the render to meet the seconds the user has set up
     — only the cast script itself gets within limit, however, there may
     be even edits, extensions"

    The script/audio length and the slot length stay fixed. Motion blocks
    are now baked LONGER than their slot on purpose (see
    ``render_dispatcher.submit_t2v`` + ``MOTION_OVERSHOOT_FACTOR``), so by
    the time a baked clip reaches this module it is at or above slot
    length. We trim the surplus and never pad.

What this module does NOT do anymore (deleted in Phase 1):

    * No ping-pong loop. A forward→reverse→forward loop made the avatar
      visibly "walk backwards" — the exact artefact the overshoot strategy
      eliminates.
    * No short-tail-reverse. Same backwards-motion defect at smaller scale.
    * No frame-clone / ``tpad=stop_mode=clone`` micro-pad. A held last
      frame read as a "freeze" in production renders.
    * No I2V re-bake ladder. The overshoot makes every motion clip
      arrive ≥ slot, so there is nothing to re-bake up to length.
    * No ``setpts`` video stretch to fill a slot. (The only permitted
      ``setpts*`` retime is the bounded Phase 1.3 micro-slowdown for a
      ≤5% motion *shortfall*, which lives behind ``apply_micro_slowdown``
      below and never touches speaking blocks.)

Trim direction: the surplus is removed from the HEAD of the clip
(``trim=start={overshoot_s}:duration={slot_s}``). Trimming the head keeps
the clip's tail — the last frame the viewer sees before the cut to the
next block — aligned with the natural end of the generated motion, which
reads more continuous than a hard cut in the middle of an action.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

import sentry_sdk

logger = logging.getLogger(__name__)


_FFPROBE_TIMEOUT_S = 15.0

# Phase 1.3 micro-slowdown. When a motion clip is SHORTER than its slot by
# a small fraction (≤ this pct), we retime the video with
# ``setpts=(slot/clip)*PTS`` so it lands exactly on slot without padding or
# reversing. This is the ONLY permitted ``setpts*`` retime in the render
# path. It is bounded (≤5%), motion-only, and never applied to speaking
# blocks (where any audio/video retime would desync the lips).
MOTION_MICRO_SETPTS_ENABLED = (
    os.environ.get("MOTION_MICRO_SETPTS_ENABLED", "true").lower()
    not in ("0", "false", "no")
)
_MICRO_SETPTS_MAX_PCT = float(
    os.environ.get("MOTION_MICRO_SETPTS_MAX_SHORTFALL", "0.05")
)

# PR-D: when a speaking block's shortfall is small (slot - clip < this many
# seconds) we allow a MORE aggressive micro-slowdown cap, because up to ~10%
# slowdown is imperceptible for short audio-led blocks and is always preferable
# to the visible tail-freeze artefact. ``MICRO_SLOWDOWN_MAX_PCT`` is expressed
# as a percent (10 → 0.10) to match the env-var name in the brief.
_MICRO_SLOWDOWN_MAX_PCT = float(
    os.environ.get("MICRO_SLOWDOWN_MAX_PCT", "10")
) / 100.0
# Below this absolute shortfall the aggressive cap applies; above it we fall
# back to the conservative ``_MICRO_SETPTS_MAX_PCT`` so longer blocks are not
# noticeably slowed.
_MICRO_SLOWDOWN_AGGRESSIVE_MAX_GAP_S = float(
    os.environ.get("MICRO_SLOWDOWN_AGGRESSIVE_MAX_GAP_SEC", "0.6")
)

# PR-D: replace the visible tail freeze-frame hold with a short reversed loop
# of the clip's tail. Holding the last frame (``tpad=stop_mode=clone``) reads
# as a playback stutter; bouncing the last ~0.5s of footage forward→reverse
# gives natural motion in the gap. Bounded to small gaps — a long reverse would
# resurface the "walks backwards" artefact the overshoot strategy removed.
TAIL_REVERSE_LOOP_ENABLED = (
    os.environ.get("TAIL_REVERSE_LOOP_ENABLED", "1").strip().lower()
    not in ("0", "false", "no", "off")
)
_TAIL_REVERSE_LOOP_MAX_GAP_S = float(
    os.environ.get("TAIL_REVERSE_LOOP_MAX_GAP_SEC", "1.5")
)
# Length of footage taken from the clip's tail to build the reversed bounce.
_TAIL_REVERSE_SEGMENT_S = float(
    os.environ.get("TAIL_REVERSE_SEGMENT_SEC", "0.5")
)

# PR-D: when the speaking clip is only marginally short of its slot (audio
# slightly shorter than video slot), trim the trailing SILENCE of the audio to
# match the video rather than padding the video. NEVER cut the cast script —
# only trailing silence/breath after speech ends is removed, within this
# tolerance. Above the tolerance we extend the video as before.
_AUDIO_TRIM_TOLERANCE_S = float(
    os.environ.get("AUDIO_TRIM_TRAILING_SILENCE_TOLERANCE_SEC", "0.3")
)
# Silence detection threshold for the trailing-silence trim (matches the
# lipsync-prep convention: anything quieter than this sustained at the tail is
# breath/room tone padding, not speech).
_AUDIO_SILENCE_DB = os.environ.get("AUDIO_TRIM_SILENCE_DB", "-50dB")

# Audio-under-slot video extension (user rule: never cut the render to the
# user slot — extend the video to fill it). When a SPEAKING bake is shorter
# than its (user-defined) slot we extend the VIDEO to slot length without
# ever shrinking the slot. The audio stays the TTS audio — the trailing
# video portion has no speech, only silence (apad). Extension ladder, in
# priority order (PR-D reorder — freeze is now a last-resort fallback):
#   1. setpts micro-slowdown — imperceptible. Aggressive 10% cap for short
#      gaps (< _MICRO_SLOWDOWN_AGGRESSIVE_MAX_GAP_S), else the 5% cap.
#   2. audio-trim-trailing-silence — when within _AUDIO_TRIM_TOLERANCE_S of
#      slot and the audio has trailing silence, trim the SILENCE (never the
#      script) so audio + video land on slot together.
#   3. tail reverse-loop — bounce the last ~0.5s of footage forward→reverse
#      across the gap (natural motion, no freeze) when gap ≤
#      _TAIL_REVERSE_LOOP_MAX_GAP_S.
#   4. tail freeze-frame hold (tpad=stop_mode=clone) — last-resort fallback,
#      only when the reverse-loop is disabled / too large / fails. Stays
#      within _SPEAKING_TAIL_HOLD_MAX_S so the validator's tail gate accepts it.
#   5. loop the clip — gives motion across the whole slot, no freeze.
# Env-overridable for tuning without a redeploy.
SPEAKING_EXTEND_TO_SLOT_ENABLED = (
    os.environ.get("SPEAKING_EXTEND_TO_SLOT_ENABLED", "true").strip().lower()
    not in ("0", "false", "no", "off")
)
# Longest tail freeze-frame hold we will use before preferring a loop. Kept
# in sync with the validator's tail-freeze tolerance
# (CLIP_VALIDATE_TAIL_FREEZE_MAX_S) so a tail-hold we produce here always
# passes Phase 3 validation.
_SPEAKING_TAIL_HOLD_MAX_S = float(
    os.environ.get("SPEAKING_TAIL_HOLD_MAX_S", "1.5")
)


def _probe_duration_s(path: str) -> float:
    """Return the container duration of ``path`` in seconds, or 0.0 on probe failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_FFPROBE_TIMEOUT_S, check=False,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return 0.0
    if result.returncode != 0:
        return 0.0
    out = (result.stdout or "").strip()
    try:
        return float(out)
    except ValueError as e:
        sentry_sdk.capture_exception(e)
        return 0.0


def probe_frame_counted_duration_s(path: str, *, target_fps: int = 30) -> float:
    """Return the *frame-counted* video duration of ``path`` in seconds.

    Container metadata (``format=duration``) reports the duration declared in
    the file header, which can lie: a ``-t`` flag and a ``tpad`` filter that
    silently drops its appended frames both leave the header at the requested
    length while the real video stream stops short. This helper decodes every
    frame (``-count_frames``) and derives the duration from the actual frame
    count, so a clip whose stream ends early is reported at its TRUE length.

    Falls back to the stream ``duration`` field, then to 0.0, on probe
    failure. ``target_fps`` is used to convert the frame count to seconds when
    the stream's own frame rate is unavailable.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration,nb_read_frames,avg_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_FFPROBE_TIMEOUT_S, check=False,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return 0.0
    if result.returncode != 0:
        return 0.0
    lines = (result.stdout or "").strip().splitlines()
    # Field order matches the -show_entries list: duration, nb_read_frames,
    # avg_frame_rate. ffprobe omits unreadable fields, so parse defensively.
    stream_dur = 0.0
    nb_frames = 0
    fps = float(target_fps) if target_fps > 0 else 30.0
    for line in lines:
        line = line.strip()
        if not line or line == "N/A":
            continue
        if "/" in line:  # avg_frame_rate like "30/1"
            try:
                num, den = line.split("/", 1)
                num_f, den_f = float(num), float(den)
                if den_f > 0 and num_f > 0:
                    fps = num_f / den_f
            except ValueError as e:
                sentry_sdk.capture_exception(e)
                continue
        elif "." in line:  # stream duration (float seconds)
            try:
                stream_dur = float(line)
            except ValueError as e:
                sentry_sdk.capture_exception(e)
                continue
        else:  # integer frame count
            try:
                nb_frames = int(line)
            except ValueError as e:
                sentry_sdk.capture_exception(e)
                continue
    if nb_frames > 0 and fps > 0:
        return nb_frames / fps
    return stream_dur


def _head_trim_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    target_fps: int,
    timeout_s: float,
) -> None:
    """Trim the over-long bake down to exactly ``slot_s`` from the head.

    ``trim=start={overshoot}:duration={slot}`` drops the leading surplus
    and keeps the final ``slot_s`` seconds. Audio is trimmed by the same
    window so the lipsynced voice (if present) stays aligned to the frames
    that survive. ``setpts``/``asetpts`` rebase timestamps to 0 after the
    cut so the concat join downstream sees no PTS gap.
    """
    overshoot_s = max(0.0, bake_s - slot_s)
    v_filter = (
        f"[0:v]fps={target_fps},"
        f"trim=start={overshoot_s:.3f}:duration={slot_s:.3f},"
        f"setpts=PTS-STARTPTS[v]"
    )
    a_filter = (
        f"[0:a]atrim=start={overshoot_s:.3f}:duration={slot_s:.3f},"
        f"asetpts=PTS-STARTPTS,aresample=48000,"
        f"aformat=channel_layouts=stereo[a]"
    )
    filter_complex = f"{v_filter};{a_filter}"
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{slot_s:.3f}",
        out_path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if result.returncode != 0 or not os.path.exists(out_path):
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"head-trim ffmpeg failed (rc={result.returncode}): {stderr_tail}"
        )


def _topup_trim_to_slot_frames(
    *,
    out_path: str,
    slot_s: float,
    target_fps: int,
    block_id: str,
    render_id: str,
) -> None:
    """Pad a head-trimmed clip up to the exact ``round(slot * fps)`` frame count.

    Seeking into a non-30fps source and trimming on a frame boundary can leave
    the head-trim output 1–4 frames short of the slot's true frame count, which
    the frame-counted Phase 3 validator then rejects as a duration undershoot.
    This step probes the trimmed output and, when it lands short, tops it up
    with the PR-H extend ladder (``extend_video_bytes_to_duration``) so the clip
    carries exactly the slot's frame count. The top-up target is the frame-exact
    ``target_frames / fps``. Extension never shortens, so an on-target (or
    over-target) clip is left untouched.
    """
    fps = float(target_fps) if target_fps > 0 else 30.0
    target_frames = round(slot_s * fps)
    target_s = target_frames / fps
    actual_s = probe_frame_counted_duration_s(out_path, target_fps=target_fps)
    actual_frames = round(actual_s * fps)
    if actual_frames >= target_frames:
        return

    logger.info(
        "[topup] block %s render %s: head-trim landed at %d frames "
        "(%.3fs), slot needs %d frames (%.3fs); padding tail to slot",
        block_id, render_id, actual_frames, actual_s,
        target_frames, target_s,
    )
    try:
        with open(out_path, "rb") as f:
            trimmed_bytes = f.read()
        padded_bytes = extend_video_bytes_to_duration(
            video_bytes=trimmed_bytes,
            target_s=target_s,
            target_fps=target_fps,
            block_id=block_id,
            render_id=render_id,
        )
        if padded_bytes and padded_bytes != trimmed_bytes:
            with open(out_path, "wb") as f:
                f.write(padded_bytes)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "[topup] block %s render %s: head-trim top-up raised (%s); "
            "leaving head-trim output as is",
            block_id, render_id, e,
        )


def _micro_slowdown_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Retime a slightly-short MOTION clip up to ``slot_s`` (Phase 1.3).

    Applies ``setpts=(slot/clip)*PTS`` to the video only. Audio is
    silence-padded with ``apad`` rather than retimed — a motion block has
    no lipsynced voice to desync, and stretching audio would pitch-shift
    it. The slowdown factor is bounded by ``_MICRO_SETPTS_MAX_PCT`` at the
    call site, so the visual ramp is imperceptible.

    Emits a Sentry breadcrumb + INFO log on activation so the (rare) micro
    -slowdown path is auditable per render.
    """
    factor = slot_s / bake_s if bake_s > 0 else 1.0
    sentry_sdk.add_breadcrumb(
        category="render.micro_slowdown",
        message=(
            f"block {block_id} render {render_id}: micro-slowdown "
            f"bake={bake_s:.3f}s → slot={slot_s:.3f}s factor={factor:.4f}"
        ),
        level="info",
    )
    logger.info(
        "[micro-slowdown] block %s render %s: setpts factor=%.4f "
        "(bake=%.3fs → slot=%.3fs)",
        block_id, render_id, factor, bake_s, slot_s,
    )
    v_filter = (
        f"[0:v]fps={target_fps},setpts={factor:.6f}*PTS,"
        f"trim=duration={slot_s:.3f},setpts=PTS-STARTPTS[v]"
    )
    a_filter = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={slot_s:.3f}[a]"
    )
    filter_complex = f"{v_filter};{a_filter}"
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{slot_s:.3f}",
        out_path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if result.returncode != 0 or not os.path.exists(out_path):
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"micro-slowdown ffmpeg failed (rc={result.returncode}): "
            f"{stderr_tail}"
        )


def _tail_freeze_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Hold the last frame of a SPEAKING clip until the clip reaches ``slot_s``.

    PR-D: this is now the LAST-RESORT fallback, used only when the reverse-loop
    tier is disabled, the gap is too large for it, or it failed — the held tail
    is the visible "freeze" artefact PR-D set out to avoid, so prefer the
    reverse-loop. ``tpad=stop_mode=clone:stop_duration=<delta>`` clones the final
    frame for
    the trailing ``delta`` seconds. The held tail carries no speech, so the
    audio is silence-padded (``apad``) to the same length rather than retimed
    — retiming would desync the lipsynced voice. The caller only takes this
    path when ``delta`` is within the validator's tail-freeze tolerance, so
    the head keeps full motion and the freeze gate accepts the clip.
    """
    delta = max(0.0, slot_s - bake_s)
    logger.info(
        "[extend] block %s render %s: tail freeze-frame hold "
        "bake=%.3fs → slot=%.3fs (hold=%.3fs)",
        block_id, render_id, bake_s, slot_s, delta,
    )
    v_filter = (
        f"[0:v]fps={target_fps},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={delta:.3f},"
        f"trim=duration={slot_s:.3f},setpts=PTS-STARTPTS[v]"
    )
    a_filter = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={slot_s:.3f}[a]"
    )
    filter_complex = f"{v_filter};{a_filter}"
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{slot_s:.3f}",
        out_path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if result.returncode != 0 or not os.path.exists(out_path):
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"tail-freeze ffmpeg failed (rc={result.returncode}): {stderr_tail}"
        )

    # The container can report slot_s in its header while the real frame
    # stream stops at bake_s — tpad's cloned frames are silently dropped by
    # some encoders. Verify with a frame-COUNTED probe (not container
    # metadata) and rebuild from a still image if the stream is short.
    frame_dur = probe_frame_counted_duration_s(out_path, target_fps=target_fps)
    floor_s = slot_s - (1.0 / float(target_fps if target_fps > 0 else 30))
    if frame_dur < floor_s:
        logger.warning(
            "[extend] block %s render %s: tail-freeze produced %.3fs of frames "
            "(target=%.3fs, floor=%.3fs) — tpad frames were dropped; "
            "rebuilding from last-frame still image",
            block_id, render_id, frame_dur, slot_s, floor_s,
        )
        _freeze_via_still_image(
            src_path=src_path,
            out_path=out_path,
            bake_s=bake_s,
            slot_s=slot_s,
            hold_s=delta,
            target_fps=target_fps,
            timeout_s=timeout_s,
            block_id=block_id,
            render_id=render_id,
        )


def _freeze_via_still_image(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    hold_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Rebuild a slot-length clip when ``tpad`` failed to extend the stream.

    Extracts the last decoded frame as a still image, encodes a ``hold_s``
    freeze segment from it at the source resolution, and concats it after the
    original clip. The audio is silence-padded to slot length. This path is the
    guaranteed-correct fallback: the freeze frames are real encoded frames, not
    filter-appended ones an encoder can drop.
    """
    logger.info(
        "[extend] block %s render %s: still-image freeze rebuild "
        "bake=%.3fs → slot=%.3fs (hold=%.3fs)",
        block_id, render_id, bake_s, slot_s, hold_s,
    )
    work = tempfile.mkdtemp(prefix=f"freeze_still_{block_id}_")
    try:
        last_png = os.path.join(work, "last.png")
        freeze_mp4 = os.path.join(work, "freeze.mp4")
        head_mp4 = os.path.join(work, "head.mp4")
        concat_list = os.path.join(work, "concat.txt")

        width, height = _probe_video_size(src_path)

        # 1. Grab the final frame as a still image.
        grab = subprocess.run(
            [
                "ffmpeg", "-y", "-sseof", "-0.2", "-i", src_path,
                "-update", "1", "-frames:v", "1", last_png,
            ],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
        if grab.returncode != 0 or not os.path.exists(last_png):
            raise RuntimeError(
                f"still-image grab failed (rc={grab.returncode}): "
                f"{(grab.stderr or '')[-800:]}"
            )

        # 2. Re-encode the head (original clip) to canvas codec, no audio.
        scale_vf = (
            f"fps={target_fps},setpts=PTS-STARTPTS"
            if width <= 0 or height <= 0
            else f"scale={width}:{height},fps={target_fps},setpts=PTS-STARTPTS"
        )
        head = subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-an", "-vf", scale_vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
                head_mp4,
            ],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
        if head.returncode != 0 or not os.path.exists(head_mp4):
            raise RuntimeError(
                f"head re-encode failed (rc={head.returncode}): "
                f"{(head.stderr or '')[-800:]}"
            )

        # 3. Build the freeze segment from the still image.
        freeze_vf = (
            f"fps={target_fps}"
            if width <= 0 or height <= 0
            else f"scale={width}:{height},fps={target_fps}"
        )
        freeze = subprocess.run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", last_png,
                "-t", f"{max(hold_s, 1.0 / float(target_fps)):.3f}",
                "-vf", freeze_vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
                freeze_mp4,
            ],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
        if freeze.returncode != 0 or not os.path.exists(freeze_mp4):
            raise RuntimeError(
                f"freeze segment build failed (rc={freeze.returncode}): "
                f"{(freeze.stderr or '')[-800:]}"
            )

        # 4. Concat head + freeze, re-mux the original (silence-padded) audio,
        #    and hard-cap to slot length.
        with open(concat_list, "w") as fh:
            fh.write(f"file '{head_mp4}'\n")
            fh.write(f"file '{freeze_mp4}'\n")
        concat = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-i", src_path,
                "-filter_complex",
                f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"apad=whole_dur={slot_s:.3f}[a]",
                "-map", "0:v:0", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                "-t", f"{slot_s:.3f}",
                out_path,
            ],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
        if concat.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(
                f"freeze concat failed (rc={concat.returncode}): "
                f"{(concat.stderr or '')[-800:]}"
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _probe_video_size(path: str) -> tuple[int, int]:
    """Return ``(width, height)`` of the first video stream, or ``(0, 0)``."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_FFPROBE_TIMEOUT_S, check=False,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return (0, 0)
    if result.returncode != 0:
        return (0, 0)
    vals = (result.stdout or "").strip().splitlines()
    try:
        return (int(vals[0]), int(vals[1]))
    except (IndexError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return (0, 0)


def _loop_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Loop a SPEAKING clip until it fills ``slot_s`` (motion, no freeze).

    Used when the shortfall is too large for a tail freeze-frame hold within
    the validator's tail tolerance. Looping the speaking clip keeps motion
    across the whole slot (avatar idle/blink) and avoids a long frozen tail.
    The audio is NOT looped — the lipsynced voice plays once and the trailing
    loops are silence-padded (``apad``) so no speech is duplicated.
    """
    logger.info(
        "[extend] block %s render %s: loop clip to fill slot "
        "bake=%.3fs → slot=%.3fs",
        block_id, render_id, bake_s, slot_s,
    )
    # -stream_loop on the video input repeats it; we then trim to slot.
    v_filter = (
        f"[0:v]fps={target_fps},setpts=PTS-STARTPTS,"
        f"trim=duration={slot_s:.3f},setpts=PTS-STARTPTS[v]"
    )
    a_filter = (
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={slot_s:.3f}[a]"
    )
    filter_complex = f"{v_filter};{a_filter}"
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", src_path,  # [0] looped video source
        "-i", src_path,                          # [1] single-pass audio source
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{slot_s:.3f}",
        out_path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if result.returncode != 0 or not os.path.exists(out_path):
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"loop-to-slot ffmpeg failed (rc={result.returncode}): {stderr_tail}"
        )


def _tail_reverse_loop_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Fill the slot gap with a reversed bounce of the clip's tail (PR-D).

    Replaces the visible ``tpad=stop_mode=clone`` freeze. Instead of holding the
    last frame, we take the final ``_TAIL_REVERSE_SEGMENT_S`` of footage,
    reverse it, and loop the resulting forward→reverse bounce across the gap so
    the avatar keeps subtle natural motion instead of freezing. The bounce only
    runs over the short trailing gap (caller bounds it to
    ``_TAIL_REVERSE_LOOP_MAX_GAP_S``), so it never resurfaces the full-clip
    "walks backwards" artefact. The lipsynced audio plays once and the trailing
    bounce is silence-padded (``apad``) so no speech is duplicated.
    """
    fps = float(target_fps if target_fps > 0 else 30)
    delta = max(0.0, slot_s - bake_s)
    seg = min(_TAIL_REVERSE_SEGMENT_S, bake_s)
    seg = max(seg, 1.0 / fps)
    seg_start = max(0.0, bake_s - seg)
    # The bounce unit is forward(seg) + reverse(seg) = 2*seg seconds. Loop it
    # just enough times to cover `delta`, then trim to exact. The ``loop``
    # filter buffers ``size`` frames, so ``size`` MUST be the bounce's own
    # frame count (a small number) — a large ``size`` (e.g. 32767) makes ffmpeg
    # buffer thousands of frames and hang. ``loop`` count is the number of EXTRA
    # repeats after the first pass.
    bounce_frames = max(1, int(round(2.0 * seg * fps)))
    loops_needed = max(0, int(delta / max(2.0 * seg, 1.0 / fps)) + 1)
    logger.info(
        "[extend] block %s render %s: tail reverse-loop "
        "bake=%.3fs → slot=%.3fs (gap=%.3fs, seg=%.3fs, loops=%d)",
        block_id, render_id, bake_s, slot_s, delta, seg, loops_needed,
    )
    # [0] full clip → original forward body; its tail segment, reversed, is the
    # bounce unit. We build a forward→reverse bounce of the tail segment, loop
    # it to cover `delta`, and concat after the full clip. Audio is taken once
    # from the original clip and silence-padded to slot.
    #   v0  : the full forward clip (zero-based)
    #   tseg: the trailing `seg` seconds of the clip
    #   tbnc: tseg followed by its reverse (a seamless bounce, no jump cut)
    #   vfill: tbnc looped/trimmed to exactly `delta`
    #   vout : v0 ++ vfill, capped to slot
    v_filter = (
        f"[0:v]fps={target_fps},setpts=PTS-STARTPTS,split=2[v0src][vtailsrc];"
        f"[vtailsrc]trim=start={seg_start:.3f},setpts=PTS-STARTPTS,"
        f"split=2[tfwd][trevsrc];"
        f"[trevsrc]reverse[trev];"
        f"[tfwd][trev]concat=n=2:v=1:a=0,setpts=PTS-STARTPTS[tbnc];"
        f"[tbnc]loop=loop={loops_needed}:size={bounce_frames}:start=0,"
        f"trim=duration={delta:.3f},setpts=PTS-STARTPTS[vfill];"
        f"[v0src][vfill]concat=n=2:v=1:a=0,"
        f"trim=duration={slot_s:.3f},setpts=PTS-STARTPTS[v]"
    )
    a_filter = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={slot_s:.3f}[a]"
    )
    filter_complex = f"{v_filter};{a_filter}"
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{slot_s:.3f}",
        out_path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if result.returncode != 0 or not os.path.exists(out_path):
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"tail-reverse-loop ffmpeg failed (rc={result.returncode}): "
            f"{stderr_tail}"
        )

    # The reverse/loop/concat chain can drop frames on some encoders just like
    # tpad does. Verify with a frame-COUNTED probe and fall back to the
    # guaranteed-correct still-image freeze if the stream came up short.
    frame_dur = probe_frame_counted_duration_s(out_path, target_fps=target_fps)
    floor_s = slot_s - (1.0 / float(target_fps if target_fps > 0 else 30))
    if frame_dur < floor_s:
        logger.warning(
            "[extend] block %s render %s: tail reverse-loop produced %.3fs of "
            "frames (target=%.3fs, floor=%.3fs); falling back to still-image "
            "freeze",
            block_id, render_id, frame_dur, slot_s, floor_s,
        )
        _freeze_via_still_image(
            src_path=src_path,
            out_path=out_path,
            bake_s=bake_s,
            slot_s=slot_s,
            hold_s=delta,
            target_fps=target_fps,
            timeout_s=timeout_s,
            block_id=block_id,
            render_id=render_id,
        )


def _audio_tail_silence_s(path: str, *, silence_db: str, timeout_s: float) -> float:
    """Return the seconds of trailing silence at the END of ``path``'s audio.

    Uses ``silencedetect`` and reports ``total_duration - last_silence_start``
    when the file ends inside a detected silence run. Returns 0.0 when there is
    no trailing silence or on any probe failure (callers then leave the audio
    untouched). This only ever measures silence AFTER speech ends — it never
    inspects or cuts the spoken portion.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", path,
        "-af", f"silencedetect=noise={silence_db}:d=0.1",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return 0.0
    stderr = result.stderr or ""
    total = _probe_duration_s(path)
    if total <= 0:
        return 0.0
    last_start = None
    last_end = None
    for line in stderr.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                last_start = float(line.split("silence_start:")[1].strip().split()[0])
                last_end = None
            except (IndexError, ValueError) as e:
                sentry_sdk.capture_exception(e)
                continue
        elif "silence_end:" in line:
            try:
                last_end = float(line.split("silence_end:")[1].strip().split()[0])
            except (IndexError, ValueError) as e:
                sentry_sdk.capture_exception(e)
                continue
    # A trailing silence is one whose start has no matching end before EOF.
    if last_start is not None and (last_end is None or last_end < last_start):
        return max(0.0, total - last_start)
    return 0.0


def _audio_trim_trailing_silence_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    trailing_silence_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Trim trailing SILENCE off the audio so a near-slot clip lands on slot.

    Used when the clip is only marginally short of its slot (within
    ``_AUDIO_TRIM_TOLERANCE_S``) AND the audio carries at least that much
    trailing silence after speech. We cut only the trailing silence/breath
    padding from the AUDIO — never the spoken script — and head-trim the video
    by the same surplus so video and audio land together on exactly ``slot_s``.

    The amount removed is bounded by the smaller of the video surplus
    (``bake_s - slot_s`` would be negative here, so really the slot/clip gap is
    on the audio side) and the measured trailing silence, so speech is never
    touched.
    """
    # How much we can shave: at most the detected trailing silence, and never
    # more than enough to bring the clip to slot. Because bake_s < slot_s here,
    # the audio is what overruns relative to the (shorter) video; we trim the
    # silence tail so the audio matches the video length, then the clip as a
    # whole sits at the video length which we pad up to slot with the cheaper
    # tiers. In practice the caller only routes here when (slot - bake) is tiny,
    # so trimming trailing silence closes the gap without any video fill.
    cut_s = min(trailing_silence_s, _AUDIO_TRIM_TOLERANCE_S)
    keep_audio_s = max(0.0, bake_s - cut_s)
    logger.info(
        "[extend] block %s render %s: trimming %.3fs trailing audio silence "
        "(bake=%.3fs → keep audio %.3fs, slot=%.3fs) — script untouched",
        block_id, render_id, cut_s, bake_s, keep_audio_s, slot_s,
    )
    # Video: keep full motion, hold/extend the small remaining gap with the
    # reverse-loop tier so there is no freeze; audio: hard-trim the trailing
    # silence so the spoken portion ends flush with the motion.
    v_filter = (
        f"[0:v]fps={target_fps},setpts=PTS-STARTPTS,"
        f"trim=duration={slot_s:.3f},setpts=PTS-STARTPTS[v]"
    )
    a_filter = (
        f"[0:a]atrim=duration={keep_audio_s:.3f},asetpts=PTS-STARTPTS,"
        f"aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={slot_s:.3f}[a]"
    )
    filter_complex = f"{v_filter};{a_filter}"
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(target_fps), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{slot_s:.3f}",
        out_path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if result.returncode != 0 or not os.path.exists(out_path):
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"audio-trim-trailing-silence ffmpeg failed "
            f"(rc={result.returncode}): {stderr_tail}"
        )


def _extend_speaking_to_slot(
    *,
    src_path: str,
    out_path: str,
    bake_s: float,
    slot_s: float,
    target_fps: int,
    timeout_s: float,
    block_id: str,
    render_id: str,
) -> None:
    """Extend a SPEAKING clip up to ``slot_s`` (user rule: never cut the slot).

    PR-D ladder, in priority order:
      1. ``setpts`` micro-slowdown. The cap is the more aggressive
         ``_MICRO_SLOWDOWN_MAX_PCT`` (default 10%) when the absolute shortfall
         is < ``_MICRO_SLOWDOWN_AGGRESSIVE_MAX_GAP_S`` (short audio-led blocks,
         where ≤10% retime is imperceptible), else the conservative
         ``_MICRO_SETPTS_MAX_PCT`` (5%).
      2. Audio-trim-trailing-silence: when the clip is within
         ``_AUDIO_TRIM_TOLERANCE_S`` of the slot AND the audio carries trailing
         silence, cut the trailing SILENCE (never the script) so audio + video
         land together on slot.
      3. Tail reverse-loop: bounce the last ``_TAIL_REVERSE_SEGMENT_S`` of
         footage forward→reverse across the gap (subtle natural motion, no
         freeze) when the gap ≤ ``_TAIL_REVERSE_LOOP_MAX_GAP_S``.
      4. Freeze fallback: the still-image / tail-hold freeze, only when the
         reverse-loop is disabled or the gap is too large for it.
      5. Loop the whole clip otherwise (large gap, motion across the slot).
    """
    shortfall_pct = ((slot_s - bake_s) / slot_s) if slot_s > 0 else 0.0
    delta = max(0.0, slot_s - bake_s)

    # Tier 1: micro-slowdown, with the aggressive cap for short gaps.
    micro_cap = (
        _MICRO_SLOWDOWN_MAX_PCT
        if delta < _MICRO_SLOWDOWN_AGGRESSIVE_MAX_GAP_S
        else _MICRO_SETPTS_MAX_PCT
    )
    if MOTION_MICRO_SETPTS_ENABLED and 0 < shortfall_pct <= micro_cap:
        _micro_slowdown_to_slot(
            src_path=src_path,
            out_path=out_path,
            bake_s=bake_s,
            slot_s=slot_s,
            target_fps=target_fps,
            timeout_s=timeout_s,
            block_id=block_id,
            render_id=render_id,
        )
        return

    # Tier 2: trim trailing audio silence when the gap is tiny and the audio
    # actually has trailing silence to give back (never cuts the script).
    if 0 < delta <= _AUDIO_TRIM_TOLERANCE_S:
        trailing_silence_s = _audio_tail_silence_s(
            src_path, silence_db=_AUDIO_SILENCE_DB, timeout_s=timeout_s
        )
        if trailing_silence_s >= delta:
            try:
                _audio_trim_trailing_silence_to_slot(
                    src_path=src_path,
                    out_path=out_path,
                    bake_s=bake_s,
                    slot_s=slot_s,
                    trailing_silence_s=trailing_silence_s,
                    target_fps=target_fps,
                    timeout_s=timeout_s,
                    block_id=block_id,
                    render_id=render_id,
                )
                return
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning(
                    "[extend] block %s render %s: audio-trim tier failed (%s); "
                    "falling through to motion fill",
                    block_id, render_id, e,
                )

    # Tier 3: reversed-tail bounce across a small gap (replaces the freeze).
    if TAIL_REVERSE_LOOP_ENABLED and 0 < delta <= _TAIL_REVERSE_LOOP_MAX_GAP_S:
        try:
            _tail_reverse_loop_to_slot(
                src_path=src_path,
                out_path=out_path,
                bake_s=bake_s,
                slot_s=slot_s,
                target_fps=target_fps,
                timeout_s=timeout_s,
                block_id=block_id,
                render_id=render_id,
            )
            return
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(
                "[extend] block %s render %s: tail reverse-loop failed (%s); "
                "falling back to freeze",
                block_id, render_id, e,
            )

    # Tier 4: freeze fallback (reverse-loop disabled, too large for it, or it
    # failed). Kept within the validator's tail tolerance.
    if delta <= _SPEAKING_TAIL_HOLD_MAX_S:
        _tail_freeze_to_slot(
            src_path=src_path,
            out_path=out_path,
            bake_s=bake_s,
            slot_s=slot_s,
            target_fps=target_fps,
            timeout_s=timeout_s,
            block_id=block_id,
            render_id=render_id,
        )
        return

    # Tier 5: loop the whole clip (large gap; motion across the slot).
    _loop_to_slot(
        src_path=src_path,
        out_path=out_path,
        bake_s=bake_s,
        slot_s=slot_s,
        target_fps=target_fps,
        timeout_s=timeout_s,
        block_id=block_id,
        render_id=render_id,
    )


async def trim_block_to_slot(
    *,
    bake_bytes: bytes,
    slot_s: float,
    target_width: int,
    target_height: int,
    target_fps: int,
    block_id: str,
    render_id: str,
    is_motion: bool = False,
    **_legacy_kwargs,
) -> bytes:
    """Conform a baked block to exactly ``slot_s``.

    The overshoot strategy guarantees motion bakes arrive ≥ slot, so the
    normal path is a head-trim. Cases:

      * bake ≥ slot (the expected case): head-trim the surplus.
      * bake slightly < slot AND ``is_motion`` AND micro-slowdown enabled
        AND shortfall ≤ 5%: apply the bounded Phase 1.3 micro-slowdown.
      * bake < slot AND speaking (``not is_motion``) AND
        ``SPEAKING_EXTEND_TO_SLOT_ENABLED``: EXTEND the video up to slot via
        ``_extend_speaking_to_slot`` (micro-slowdown → audio-trim-trailing-
        silence → reverse-loop → freeze fallback → loop).
        This honours the user rule — the user-defined slot is NEVER shrunk;
        a short TTS clip is filled, not cut. The TTS audio is not extended;
        the trailing video is silence-padded (``apad``).
      * any other shortfall (motion, beyond micro-slowdown): a failure of the
        overshoot contract — we return the bake untrimmed and let the caller's
        gate (``validate_baked_clip``, Phase 3) fail the block.

    We never reverse a clip (the backward-walk artefact). The only retimes are
    the ≤5% micro-slowdown and the tail freeze-frame hold / loop used to fill a
    short speaking slot.

    ``**_legacy_kwargs`` swallows arguments from the removed re-bake path
    (``motion_prompt``, ``r2``) so existing call sites keep working without
    a flag-day rename.
    """
    if not bake_bytes:
        raise ValueError("trim_block_to_slot: empty bake_bytes")
    if slot_s <= 0:
        raise ValueError(f"trim_block_to_slot: invalid slot_s {slot_s}")

    tmpdir = tempfile.mkdtemp(prefix=f"trim_{block_id}_")
    try:
        bake_path = os.path.join(tmpdir, "bake.mp4")
        with open(bake_path, "wb") as f:
            f.write(bake_bytes)

        bake_s = _probe_duration_s(bake_path)
        if bake_s <= 0:
            # No probe → trust the caller's normalize step and bail.
            return bake_bytes

        out_path = os.path.join(tmpdir, "out.mp4")
        timeout_s = max(60.0, 4.0 * float(slot_s))
        overshoot_s = bake_s - slot_s
        shortfall_pct = (-overshoot_s / slot_s) if slot_s > 0 else 0.0

        if overshoot_s >= -0.05:
            # At or above slot (within a frame): head-trim the surplus.
            logger.info(
                "trim block %s render %s: bake=%.3fs slot=%.3fs "
                "overshoot=%+.3fs → head-trim",
                block_id, render_id, bake_s, slot_s, overshoot_s,
            )
            _head_trim_to_slot(
                src_path=bake_path,
                out_path=out_path,
                bake_s=bake_s,
                slot_s=slot_s,
                target_fps=target_fps,
                timeout_s=timeout_s,
            )
            _topup_trim_to_slot_frames(
                out_path=out_path,
                slot_s=slot_s,
                target_fps=target_fps,
                block_id=block_id,
                render_id=render_id,
            )
        elif (
            is_motion
            and MOTION_MICRO_SETPTS_ENABLED
            and shortfall_pct <= _MICRO_SETPTS_MAX_PCT
        ):
            # Phase 1.3: a small motion shortfall — retime up to slot.
            logger.info(
                "trim block %s render %s: bake=%.3fs slot=%.3fs "
                "shortfall=%.1f%% → micro-slowdown",
                block_id, render_id, bake_s, slot_s, shortfall_pct * 100,
            )
            _micro_slowdown_to_slot(
                src_path=bake_path,
                out_path=out_path,
                bake_s=bake_s,
                slot_s=slot_s,
                target_fps=target_fps,
                timeout_s=timeout_s,
                block_id=block_id,
                render_id=render_id,
            )
        elif (not is_motion) and SPEAKING_EXTEND_TO_SLOT_ENABLED:
            # SPEAKING bake shorter than its (user-defined) slot. The user
            # rule forbids shrinking the slot to the audio — we EXTEND the
            # video to fill the slot instead (micro-slowdown → audio-trim-
            # trailing-silence → reverse-loop → freeze fallback → loop). The
            # TTS audio is not extended; the trailing video is silence-padded
            # inside the extension helpers.
            logger.info(
                "trim block %s render %s: bake=%.3fs slot=%.3fs "
                "shortfall=%.1f%% → extend speaking video to slot "
                "(never shrink user slot)",
                block_id, render_id, bake_s, slot_s, shortfall_pct * 100,
            )
            _extend_speaking_to_slot(
                src_path=bake_path,
                out_path=out_path,
                bake_s=bake_s,
                slot_s=slot_s,
                target_fps=target_fps,
                timeout_s=timeout_s,
                block_id=block_id,
                render_id=render_id,
            )
        else:
            # Overshoot contract violated for a MOTION bake (materially
            # shorter than slot, beyond the 5% micro-slowdown). We refuse to
            # pad/reverse/stretch — return the bake as is and let the Phase 3
            # validation gate fail the block.
            sentry_sdk.capture_exception(
                RuntimeError(
                    f"trim block {block_id} render {render_id}: bake "
                    f"{bake_s:.3f}s under slot {slot_s:.3f}s by "
                    f"{shortfall_pct * 100:.1f}% (is_motion={is_motion}); "
                    f"NOT padding — block will fail validation"
                )
            )
            logger.warning(
                "trim block %s render %s: bake %.3fs under slot %.3fs "
                "(%.1f%% short, is_motion=%s); returning untrimmed bake "
                "for validation to reject",
                block_id, render_id, bake_s, slot_s,
                shortfall_pct * 100, is_motion,
            )
            return bake_bytes

        # Frame-counted duration is authoritative: the container header can
        # report slot_s while the real stream is short. Log both so a future
        # divergence is visible in production logs.
        final_s = probe_frame_counted_duration_s(out_path, target_fps=target_fps)
        container_s = _probe_duration_s(out_path)
        logger.info(
            "trim block %s render %s: final duration=%.3fs "
            "(container=%.3fs, target=%.3fs, delta=%+.3fs)",
            block_id, render_id, final_s, container_s, slot_s,
            final_s - slot_s,
        )
        with open(out_path, "rb") as f:
            return f.read()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "trim_block_to_slot block %s render %s raised (%s); "
            "returning original bake bytes",
            block_id, render_id, e,
        )
        return bake_bytes
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def extend_video_bytes_to_duration(
    *,
    video_bytes: bytes,
    target_s: float,
    target_fps: int = 30,
    block_id: str = "",
    render_id: str = "",
) -> bytes:
    """Extend a clip's VIDEO stream up to ``target_s`` (never shorten).

    Reuses the speaking-block freeze/loop ladder so the a/v reconciliation
    path in ``cast_render`` can lengthen a video that is shorter than its
    (slot-length) audio, instead of trimming the audio down. The audio is
    silence-padded to ``target_s`` by the ladder helpers. The output is
    verified with a frame-counted probe; on any failure the original bytes
    are returned so the caller can degrade gracefully.

    Returns the original bytes unchanged when the clip is already ≥ target.
    """
    if not video_bytes or target_s <= 0:
        return video_bytes

    tmpdir = tempfile.mkdtemp(prefix=f"extvid_{block_id}_")
    try:
        in_path = os.path.join(tmpdir, "in.mp4")
        out_path = os.path.join(tmpdir, "out.mp4")
        with open(in_path, "wb") as f:
            f.write(video_bytes)

        cur_s = probe_frame_counted_duration_s(in_path, target_fps=target_fps)
        if cur_s <= 0:
            return video_bytes
        if cur_s >= target_s - (1.0 / float(target_fps if target_fps > 0 else 30)):
            return video_bytes

        timeout_s = max(60.0, target_s * 5.0)
        _extend_speaking_to_slot(
            src_path=in_path,
            out_path=out_path,
            bake_s=cur_s,
            slot_s=target_s,
            target_fps=target_fps,
            timeout_s=timeout_s,
            block_id=block_id,
            render_id=render_id,
        )
        final_s = probe_frame_counted_duration_s(out_path, target_fps=target_fps)
        _fp = float(target_fps if target_fps > 0 else 30)
        # Accept a landing within 3 frames of target. The ladder's setpts
        # micro-slowdown stretches PTS but doesn't always add frames, so a
        # frame-counted probe can read ~1-2 frames short even on a "successful"
        # retime. A sub-0.1s tail difference is imperceptible, well inside the
        # validator's tail-freeze tolerance, and the bonded concat lays
        # segments end-to-end so it doesn't accumulate into visible drift —
        # keeping the ladder output beats discarding it back to the (shorter)
        # original.
        floor_s = target_s - (3.0 / _fp)
        if final_s < floor_s or not os.path.exists(out_path):
            logger.warning(
                "[extend] block %s render %s: video extension landed at %.3fs "
                "(target=%.3fs, floor=%.3fs) — returning original bytes",
                block_id, render_id, final_s, target_s, floor_s,
            )
            return video_bytes
        with open(out_path, "rb") as f:
            return f.read()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "extend_video_bytes_to_duration block %s render %s raised (%s); "
            "returning original bytes",
            block_id, render_id, e,
        )
        return video_bytes
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
