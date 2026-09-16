"""Lipsync audio preprocessing (PR #82).

Lipsync engines expect audio that is:
  * 16 kHz mono PCM WAV (Wav2Vec2 / Whisper feature extractors)
  * Loudness-normalized to ~-16 LUFS (broadcast -23 LUFS is too quiet for
    the amplitude-driven mouth gate and yields stretches of mouth-held-open)
  * Padded with ~100ms silence at the HEAD only (anchors the first
    rolling-window segment for long-clip engines so they don't mouth-hold
    at the segment boundary). The TAIL is deliberately NOT padded — a
    silent tail makes the engine generate "speaking" frames against
    silence, which shows on the final video as lips moving with no voice
    after the speech ends (regression-2b).
  * Free of >200ms trailing silence (otherwise the model continues to
    generate "speaking" frames against silence at the tail)

This module exposes a single async helper, prepare_lipsync_audio, that
downloads the source TTS audio, applies a two-pass loudnorm + edge-pad +
trailing-silence trim via ffmpeg, uploads the result to R2 under a
deterministic key, and returns the public URL.

Idempotency: the R2 key is sha1(block_id + tts_source_hash). If the
object already exists in R2 we skip the work and return its URL — so
re-renders of the same block (and same TTS source) reuse the cached
prep'd WAV.

All exceptions are captured to Sentry. The caller is expected to fall
back to the un-prepared audio URL when this helper raises.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile

import httpx
import sentry_sdk

logger = logging.getLogger(__name__)


# Loudness target. -16 LUFS integrated is the consensus target for
# lipsync feature extractors (see PR #82 research notes); -23 LUFS
# broadcast-EBU is too quiet for amplitude-driven mouth gates.
_LOUDNORM_I = -16.0
_LOUDNORM_TP = -1.5
_LOUDNORM_LRA = 11.0

# Head pad — 100ms at the START only. Short enough that the audible
# delay doesn't drift the timeline; long enough that the rolling-window
# encoders have material to anchor the FIRST segment against. We do NOT
# pad the tail: a silent tail makes the engine animate "speaking" frames
# against silence, which shows on the final video as lips moving with no
# voice after speech ends (regression-2b). The head pad stays voice-aligned
# because PR #135 rewrites the mux source to this same prepared audio.
_HEAD_PAD_S = 0.1

# Max allowed drift between the prepared output duration and the
# expected duration (input + head pad). The chain must be strictly
# duration-preserving apart from the intentional head pad AND the
# trailing-silence trim; anything beyond this margin indicates a filter
# (e.g. silenceremove) ate real speech and is treated as a defect.
#
# Was 30ms, which is incompatible with the trailing-silence trim this
# step performs on purpose. Confirmed empirically (reproducing this exact
# filter chain against real audio, and again with a synthesized clip in
# tests/unit/test_lipsync_audio_prep_duration.py): with stop_periods
# correctly negative (tail-anchored), ffmpeg's silenceremove removes
# essentially ALL of a qualifying trailing-silence run, not just an
# excess above some small buffer — so the legitimate drift for a clip
# scales with however much real trailing silence the TTS engine actually
# left (tens of ms up to a second or more is normal), not a small fixed
# constant. A tight cap rejected those as "defects" and fell back to raw
# audio on every one, which is what fed the downstream speaking-duration-
# tolerance check a mismatched clip and forced a full re-bake retry loop
# (cst_294eeaf04af2 block blk_1a61c93a5731 — a real block, no defect,
# whose correctly-trimmed output still drifted 58ms, failing the old
# 30ms cap). Now that the stop_periods sign is fixed, silenceremove is
# structurally incapable of touching non-trailing audio (the search is
# anchored to the end of the clip), so this cap's remaining job is to
# catch unrelated defects (corrupted downloads, ffmpeg crashes, a
# different filter bug) producing multi-second garbage — not to
# second-guess a legitimate trailing-silence trim. Env-overridable.
_PREP_DURATION_DRIFT_MAX_MS = 2000.0

# Trailing-silence trim. Anything quieter than -50 dB sustained for
# more than 200ms at the tail is stripped — this prevents the
# mouth-held-open artifact when the audio runs out before the video.
_TRAILING_SILENCE_THRESHOLD_DB = -50
_TRAILING_SILENCE_STOP_DURATION_S = 0.2


def _prep_duration_drift_max_s() -> float:
    """Duration-preservation tolerance in seconds, re-read per call so an
    env override set after import (tests, hot-reload) still takes effect."""
    try:
        return float(
            os.environ.get(
                "LIPSYNC_PREP_DURATION_DRIFT_MAX_MS",
                str(_PREP_DURATION_DRIFT_MAX_MS),
            )
        ) / 1000.0
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        return _PREP_DURATION_DRIFT_MAX_MS / 1000.0


def _ffmpeg_duration_per_step(audio_duration_s: float) -> float:
    """Per-ffmpeg-step subprocess timeout. Two-pass loudnorm + pad +
    trim each scale with the audio length; floor at 30s for ffmpeg
    cold-start, no cap (a 5min clip's loudnorm pass can legitimately
    take 30-40s)."""
    try:
        d = float(audio_duration_s or 0)
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        d = 0.0
    return max(30.0, d * 6.0)


def _sha1_short(*parts: str) -> str:
    """Stable short hash for idempotency keys. Truncated to 16 hex
    chars — collision probability is irrelevant at our block volume
    and the shorter key is friendlier in logs."""
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def _tts_source_hash(src_url: str) -> str:
    """Fingerprint of the TTS source URL.

    We don't hash the audio bytes themselves because the URL already
    includes the variant id / version path segment and a fresh TTS
    bake produces a new key. Hashing the URL is sufficient to bust
    the cache when the upstream audio changes, and cheap (no extra
    download).
    """
    return _sha1_short(src_url or "")


def _build_r2_key(render_id: str, block_id: str, src_url: str) -> str:
    """Deterministic R2 key for the prep'd lipsync WAV.

    Layout: lipsync_prep/<render_id>/<block_id>-<src_hash>.wav

    The src_hash is included so that if the underlying TTS audio is
    regenerated for the same block_id (variant edit, voice change),
    the cache key changes and we re-run the prep — instead of serving
    a stale prep'd WAV that points at the old phoneme sequence.
    """
    bucket = (render_id or "noctx").strip() or "noctx"
    src_hash = _tts_source_hash(src_url)
    return f"lipsync_prep/{bucket}/{block_id}-{src_hash}.wav"


def _run_ffmpeg(cmd: list[str], *, timeout_s: float, step: str) -> subprocess.CompletedProcess:
    """Run an ffmpeg command, capturing stderr for diagnostics.

    Raises RuntimeError with the tail of stderr on non-zero exit.
    """
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"ffmpeg {step} failed (rc={proc.returncode}): {tail}")
    return proc


def _parse_loudnorm_measurements(stderr: str) -> dict | None:
    """Extract the JSON block printed by ffmpeg's first-pass loudnorm.

    ffmpeg writes the measurements as a JSON blob to stderr at the end
    of the run when ``print_format=json`` is set. We grep the last
    top-level ``{...}`` out of stderr — the parser is intentionally
    forgiving because ffmpeg sometimes interleaves additional log
    lines.
    """
    if not stderr:
        return None
    matches = re.findall(r"\{[^{}]*\}", stderr, re.DOTALL)
    for blob in reversed(matches):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            sentry_sdk.capture_exception(exc)
            continue
        if "input_i" in data and "input_tp" in data:
            return data
    return None


def _first_pass_loudnorm(in_path: str, timeout_s: float) -> dict | None:
    """First loudnorm pass — measures input_i/input_tp/input_lra/
    input_thresh/target_offset and prints them as JSON.

    Output is discarded (-f null). Returns the parsed measurement
    dict, or None if ffmpeg didn't print one.
    """
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", in_path,
        "-af",
        (
            f"loudnorm=I={_LOUDNORM_I}:TP={_LOUDNORM_TP}:LRA={_LOUDNORM_LRA}:"
            "print_format=json"
        ),
        "-f", "null", "-",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        logger.warning("loudnorm first pass exited rc=%d: %s", proc.returncode, tail)
        return None
    return _parse_loudnorm_measurements(proc.stderr or "")


def _second_pass_loudnorm_filter(measurements: dict | None) -> str:
    """Build the loudnorm filter string for the second pass.

    When the first pass produced measurements, apply them as
    ``measured_*`` inputs so the second pass is linear and matches
    the target exactly. When measurements are missing (first pass
    failed or didn't emit JSON), fall back to a single-pass loudnorm
    — still better than nothing.
    """
    base = (
        f"loudnorm=I={_LOUDNORM_I}:TP={_LOUDNORM_TP}:LRA={_LOUDNORM_LRA}"
    )
    if not measurements:
        return base
    try:
        return (
            f"{base}:measured_I={float(measurements['input_i'])}"
            f":measured_TP={float(measurements['input_tp'])}"
            f":measured_LRA={float(measurements['input_lra'])}"
            f":measured_thresh={float(measurements['input_thresh'])}"
            f":offset={float(measurements.get('target_offset', 0.0))}"
            ":linear=true:print_format=summary"
        )
    except (KeyError, TypeError, ValueError) as exc:
        sentry_sdk.add_breadcrumb(
            category="lipsync_audio_prep",
            level="info",
            message="loudnorm_measurements_malformed",
            data={"exc": str(exc), "keys": sorted(list(measurements.keys()))},
        )
        return base


async def _download_source(src_url: str, dst_path: str, *, timeout_s: float) -> None:
    """Fetch the source TTS file. Follows redirects (R2 public URLs
    sometimes redirect to a signed CDN URL)."""
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.get(src_url, follow_redirects=True)
        resp.raise_for_status()
        with open(dst_path, "wb") as fh:
            fh.write(resp.content)


def _probe_duration_s(path: str) -> float:
    """Read the audio container duration via ffprobe. Returns 0.0 if
    ffprobe fails — the caller passes the result to the per-step
    timeout calculator, which floors at 30s."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
        out = (proc.stdout or "").strip()
        return float(out) if out else 0.0
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return 0.0


async def prepare_lipsync_audio(
    src_url: str,
    render_id: str,
    block_id: str,
    *,
    r2=None,
) -> str:
    """Preprocess a TTS audio URL for lipsync ingestion.

    Pipeline (all via ffmpeg, all idempotent against the source URL):
      1. Download src_url to a tmp WAV.
      2. Resample to 16 kHz mono PCM s16le.
      3. Two-pass loudnorm to -16 LUFS / -1.5 dBTP / LRA 11.
      4. Pad 100ms silence at the START only (head anchor; no tail pad).
      5. Trim trailing silence longer than 200ms below -50 dB.
      6. Assert duration ≈ input + head pad (no filter ate real speech).
      7. Upload to R2 at lipsync_prep/<render_id>/<block_id>-<srchash>.wav.

    Idempotency: if the R2 key already exists we return its public
    URL without re-running ffmpeg.

    Args:
        src_url: source TTS audio URL (16 kHz WAV or 44.1 kHz MP3).
        render_id: cast render id — used in the R2 key namespace.
        block_id: speaking block id — used in the R2 key.
        r2: optional R2StorageService instance for tests; defaults to
            the process singleton.

    Returns:
        Public CDN URL of the prep'd 16 kHz WAV.

    Raises:
        Any exception is captured to Sentry before being re-raised so
        the caller can choose to fall back to the un-prepared audio.
    """
    if not src_url:
        raise ValueError("prepare_lipsync_audio: src_url is empty")
    if not block_id:
        raise ValueError("prepare_lipsync_audio: block_id is empty")

    if r2 is None:
        try:
            from services.r2_storage import get_r2_storage_service
            r2 = get_r2_storage_service()
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise

    out_key = _build_r2_key(render_id, block_id, src_url)

    # Idempotency probe. A miss is the common path on the first bake;
    # a hit happens on retries / re-renders of the same block + audio.
    try:
        if await r2.key_exists(out_key):
            logger.info(
                "lipsync_audio_prep: cache hit for block %s → %s",
                block_id, out_key,
            )
            return r2.get_public_url(out_key)
    except Exception as exc:
        # Don't fail the whole prep on a cache probe error — fall
        # through and re-build the WAV. The breadcrumb keeps the
        # diagnostic without firing a Sentry issue on transient HEAD
        # errors.
        sentry_sdk.add_breadcrumb(
            category="lipsync_audio_prep",
            level="info",
            message="cache_probe_failed",
            data={"key": out_key, "exc": str(exc)[:200]},
        )

    with tempfile.TemporaryDirectory(prefix=f"lsprep_{block_id}_") as tmp:
        raw_path = os.path.join(tmp, "src.bin")
        resampled_path = os.path.join(tmp, "16k.wav")
        normalized_path = os.path.join(tmp, "norm.wav")
        padded_path = os.path.join(tmp, "padded.wav")
        out_path = os.path.join(tmp, "out.wav")

        try:
            # Step 1: download. We don't know the duration yet, so
            # use a conservative download timeout (5 min) — the
            # real per-step ffmpeg budgets are computed after we
            # probe the file.
            await _download_source(src_url, raw_path, timeout_s=300.0)

            dur_s = await asyncio.to_thread(_probe_duration_s, raw_path)
            step_timeout = _ffmpeg_duration_per_step(dur_s)

            # Step 2: resample to 16 kHz mono PCM. We do this in its
            # own pass (rather than as part of loudnorm) so the
            # loudnorm two-pass sees the exact bytes we'll feed
            # forward — fewer cross-filter quirks.
            await asyncio.to_thread(
                _run_ffmpeg,
                [
                    "ffmpeg", "-y", "-hide_banner", "-nostats",
                    "-i", raw_path,
                    "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    resampled_path,
                ],
                timeout_s=step_timeout, step="resample",
            )

            # Step 3: two-pass loudnorm.
            measurements = await asyncio.to_thread(
                _first_pass_loudnorm, resampled_path, step_timeout,
            )
            loudnorm_filter = _second_pass_loudnorm_filter(measurements)
            await asyncio.to_thread(
                _run_ffmpeg,
                [
                    "ffmpeg", "-y", "-hide_banner", "-nostats",
                    "-i", resampled_path,
                    "-af", loudnorm_filter,
                    "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    normalized_path,
                ],
                timeout_s=step_timeout, step="loudnorm_second_pass",
            )

            # Step 4: pad 100ms silence at the START only. adelay shifts
            # the speech right by 100ms so the rolling-window encoder has
            # an anchor for its first segment. We intentionally do NOT
            # append a tail pad (the old apad): a silent tail makes the
            # engine animate "speaking" frames against silence, which the
            # final mux shows as lips moving with no voice after speech
            # ends (regression-2b). The head pad stays voice-aligned in the
            # final video because PR #135 rewrites the mux source to this
            # same prepared audio.
            pad_ms = int(_HEAD_PAD_S * 1000)
            pad_filter = f"adelay={pad_ms}|{pad_ms}"
            await asyncio.to_thread(
                _run_ffmpeg,
                [
                    "ffmpeg", "-y", "-hide_banner", "-nostats",
                    "-i", normalized_path,
                    "-af", pad_filter,
                    "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    padded_path,
                ],
                timeout_s=step_timeout, step="edge_pad",
            )

            # Step 5: trim trailing silence > 200ms below -50 dB.
            # stop_periods must be NEGATIVE (-1) to anchor the search at
            # the END of the clip and remove only genuine trailing
            # silence. A positive stop_periods scans FORWARD from the
            # start and removes the Nth qualifying silence period
            # wherever it's first found — for TTS audio that can be a
            # mid-sentence breath/pause, and everything after it gets cut
            # too. This was previously stop_periods=1 (positive), which
            # is exactly that bug: confirmed on a real render
            # (cst_294eeaf04af2 block blk_1a61c93a5731) it matched an
            # internal pause and discarded 2.46s of real trailing speech
            # (6.526s -> 4.066s) instead of trimming the ~58ms of actual
            # trailing silence stop_periods=-1 correctly removes. With
            # the tail pad gone (Step 4) and the sign correct, this only
            # strips genuine trailing silence already in the TTS source,
            # tightening the tail so the engine stops animating the
            # moment speech ends — internal pauses (breaths, comma beats)
            # are left alone since the search never looks at them.
            silence_filter = (
                "silenceremove="
                "stop_periods=-1"
                f":stop_duration={_TRAILING_SILENCE_STOP_DURATION_S}"
                f":stop_threshold={_TRAILING_SILENCE_THRESHOLD_DB}dB"
            )
            await asyncio.to_thread(
                _run_ffmpeg,
                [
                    "ffmpeg", "-y", "-hide_banner", "-nostats",
                    "-i", padded_path,
                    "-af", silence_filter,
                    "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    out_path,
                ],
                timeout_s=step_timeout, step="trim_trailing_silence",
            )

            # Step 6: assert the chain is strictly duration-preserving
            # apart from the intentional head pad. Expected output length
            # is input + head pad; the only other length-changing filter
            # is silenceremove, which must only strip genuine trailing
            # silence (well under the tolerance). A larger drift means a
            # filter ate real speech (or added unexpected silence) — the
            # exact failure mode that produces lips-without-voice — so we
            # fail loud instead of shipping it.
            out_dur = await asyncio.to_thread(_probe_duration_s, out_path)
            expected_dur = float(dur_s or 0.0) + _HEAD_PAD_S
            drift_s = abs(out_dur - expected_dur)
            drift_max_s = _prep_duration_drift_max_s()
            if out_dur > 0 and dur_s > 0 and drift_s > drift_max_s:
                msg = (
                    f"lipsync_audio_prep duration drift: out={out_dur:.3f}s "
                    f"expected≈{expected_dur:.3f}s (in={float(dur_s):.3f}s + "
                    f"head_pad={_HEAD_PAD_S:.3f}s), drift {drift_s * 1000:.0f}ms "
                    f"> {drift_max_s * 1000:.0f}ms for block {block_id}"
                )
                err = RuntimeError(msg)
                sentry_sdk.capture_exception(err)
                raise err

            # Step 7: upload. We use upload_file (streaming) rather
            # than reading the bytes into memory — these clips can
            # run to several MB on long blocks.
            await r2.upload_file(out_path, out_key, content_type="audio/wav")

            logger.info(
                "lipsync_audio_prep: prepared block %s (dur≈%.2fs) → %s",
                block_id, dur_s, out_key,
            )
            return r2.get_public_url(out_key)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise


__all__ = ["prepare_lipsync_audio"]
