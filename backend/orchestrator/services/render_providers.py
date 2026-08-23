"""Tiered cloud-fallback render providers.

Each provider class wraps a single (step, vendor) call site behind a uniform
async interface so the orchestrator can chain them via provider_chain.try_chain
without baking vendor-specific knobs into the call sites.

Contract:
    name: str         — stable identifier, used in logs/metrics/Sentry tags
    tier: int         — 1 (cheapest / preferred) → 3 (last resort)
    is_available()    — env-var presence + optional health probe
    generate(**kwargs)— vendor-specific call; returns a dict the caller decodes

Steps covered:
    SPEAKING       — image+audio → lipsynced video (HOSTKEY → WaveSpeed → fal MuseTalk)
    TTS            — text+voice clone → audio (HOSTKEY/RunPod → Fish Audio Cloud → ElevenLabs)
    TRANSCRIPTION  — audio → segments+word timestamps (HOSTKEY WhisperX → fal Whisper)

Providers are deliberately import-light: missing API keys mean is_available() == False,
not an ImportError at module load. The orchestrator never crashes on a missing provider —
it just skips that tier.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import subprocess
import tempfile
import time

import httpx
import sentry_sdk

from services.hostkey_flags import hostkey_disabled, log_hostkey_skip

logger = logging.getLogger(__name__)


def gpu_status_stub() -> dict:
    """Return a stub ``/api/gpu-status`` response for the decommissioned
    HOSTKEY box. Used by any status probe so it reports a clean "disabled"
    state instead of failing on a connection error to the dead host."""
    return {
        "status": "disabled",
        "reason": "hostkey_decommissioned",
        "memory_used_mib": None,
        "lock": {},
    }


# ── Motion overshoot (Phase 1.1) ───────────────────────────────────────
# Strategy (locked, user-mandated): a motion clip that comes back SHORTER
# than its slot is a failure — we never pad / clone / reverse / stretch it
# up to length. Instead we deliberately request a LONGER clip from the
# motion provider and trim the surplus off the head downstream
# (services.block_extension.trim_block_to_slot). This factor is the
# overshoot: request slot * MOTION_OVERSHOOT_FACTOR seconds, snapped up to
# the provider's nearest supported duration tier.
#
# Only the FINAL segment of a multi-segment chain (Kling Elements) is
# overshot — interior segments are already exact and trimming them would
# desync the join.
MOTION_OVERSHOOT_FACTOR = float(os.environ.get("MOTION_OVERSHOOT_FACTOR", "1.15"))

# Supported provider duration tiers. A motion request is rounded UP to the
# smallest tier ≥ the overshot target so the bake is always ≥ slot.
# TODO(render): these are the currently-observed supported values; verify
# against the live fal.ai Wan 2.5 T2V / Kling 2.5 Turbo Pro API docs and
# update if the provider exposes additional tiers (Render_Quality_Duration_Validation.md §1.1).
WAN_25_T2V_DURATIONS = (5, 10)
KLING_25_TURBO_DURATIONS = (5, 10)

# Wan 2.7 image-to-video (the avatar body-motion path) accepts an integer
# number of seconds. The provider's supported range is 2..15s inclusive, so
# the tier set is every integer in that span — unlike the discrete (5, 10)
# T2V model, Wan 2.7 I2V can serve any whole-second length. This replaces the
# old ad-hoc ``max(2, min(15, ...))`` clamp in ``wan_body_motion`` so the
# overshoot + tier-snap math shares one source of truth.
WAN_27_I2V_MIN_S = int(os.environ.get("WAN_27_I2V_MIN_S", "2"))
WAN_27_I2V_MAX_S = int(os.environ.get("WAN_27_I2V_MAX_S", "15"))
WAN_27_I2V_DURATIONS = tuple(range(WAN_27_I2V_MIN_S, WAN_27_I2V_MAX_S + 1))


def _plan_wan_i2v_segments(total_seconds: float) -> list[int]:
    """Plan Wan 2.7 I2V segment durations for a slot longer than the max tier.

    Mirrors :func:`_plan_kling_segments` but for the integer-second Wan 2.7
    I2V tier set. A slot that fits a single tier (``<= WAN_27_I2V_MAX_S``)
    returns one segment; a longer slot is split into chunks that each fit the
    max tier, with the final chunk never below the min tier.

    The OVERSHOOT is applied by the caller only to the FINAL segment's tier
    selection — interior segments are exact so trimming them would desync the
    join (Render_Quality_Duration_Validation.md §1.1.3).

    Returns:
        - ``[ceil(total) clamped into [min,max]]`` for ``total <= max``.
        - For ``total > max``: a list of ints each in ``[min, max]`` summing to
          roughly ``ceil(total)``; the last segment is never ``< min`` (we
          steal from the previous chunk to keep that invariant).
    """
    if total_seconds is None or total_seconds <= 0:
        return []
    total = int(math.ceil(float(total_seconds)))
    if total <= WAN_27_I2V_MAX_S:
        return [max(WAN_27_I2V_MIN_S, min(total, WAN_27_I2V_MAX_S))]
    segs: list[int] = []
    remaining = total
    while remaining > WAN_27_I2V_MAX_S:
        segs.append(WAN_27_I2V_MAX_S)
        remaining -= WAN_27_I2V_MAX_S
    if remaining < WAN_27_I2V_MIN_S and segs:
        deficit = WAN_27_I2V_MIN_S - remaining
        segs[-1] -= deficit
        remaining = WAN_27_I2V_MIN_S
    segs.append(remaining)
    segs = [max(WAN_27_I2V_MIN_S, min(WAN_27_I2V_MAX_S, s)) for s in segs]
    assert all(WAN_27_I2V_MIN_S <= s <= WAN_27_I2V_MAX_S for s in segs), segs
    return segs


def _overshoot_target_s(slot_s: float) -> float:
    """Return ``slot_s`` scaled by :data:`MOTION_OVERSHOOT_FACTOR`.

    The result is the *raw* overshot target before tier-snapping; callers
    snap it up to a provider-supported integer via :func:`_snap_up_tier`.
    """
    try:
        s = float(slot_s)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0
    return s * MOTION_OVERSHOOT_FACTOR


def _snap_up_tier(target_s: float, tiers: tuple[int, ...]) -> int:
    """Smallest tier ≥ ``target_s``; the largest tier if none qualifies.

    Guarantees the requested provider duration is never below the overshot
    target, so the bake is always at least slot-length and the downstream
    head-trim has surplus to remove rather than a deficit to pad.
    """
    if not tiers:
        return int(math.ceil(max(0.0, target_s)))
    ordered = sorted(int(t) for t in tiers)
    for t in ordered:
        if t >= target_s - 1e-6:
            return t
    return ordered[-1]


# Minimum frozen-segment length we treat as a real freeze artifact.
# Anything shorter is within the noise floor of a still moment in
# normal speech (a held smile, a pause between syllables). The
# 0.6 s ceiling matches the visible threshold observed on render
# rnd_3e356220b834 where InfiniteTalk stopped animating mid-clip
# for 2-4 s and the freeze was immediately obvious.
_FREEZE_MIN_DURATION_S = 0.6
# freezedetect noise floor — the value matches ffmpeg's default for
# this filter and rejects sub-frame pixel jitter as motion.
_FREEZE_NOISE_THRESHOLD = "0.002"


async def _ffmpeg_freeze_detect(
    video_path: str,
    *,
    clip_duration_s: float,
) -> list[tuple[float, float]]:
    """Run ``ffmpeg -vf freezedetect`` on a local file and return any
    frozen segments as ``[(start_s, end_s), ...]``.

    Lightweight: ``-f null -`` discards the encoded output, so this is
    a decode-only pass. Timeout scales with clip duration per the
    project rule against hardcoded ffmpeg deadlines: 60 s floor +
    8x realtime ceiling (decode is ~10-20x realtime on modern CPUs,
    so 8x gives plenty of headroom for slow IO).
    """
    timeout_s = max(60.0, float(clip_duration_s or 0) * 8.0)
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", video_path,
        "-vf", f"freezedetect=n={_FREEZE_NOISE_THRESHOLD}:d={_FREEZE_MIN_DURATION_S}",
        "-an", "-f", "null", "-",
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd,
        capture_output=True, timeout=timeout_s, check=False,
    )
    stderr = (proc.stderr or b"").decode("utf-8", "replace")
    freezes: list[tuple[float, float]] = []
    current_start: float | None = None
    # freezedetect emits two log lines per frozen segment:
    #   [freezedetect @ ...] lavfi.freezedetect.freeze_start: <t>
    #   [freezedetect @ ...] lavfi.freezedetect.freeze_end: <t>
    # A trailing freeze that lasts to EOF gets a freeze_start but no
    # freeze_end — close it with the clip duration so it still counts.
    for line in stderr.splitlines():
        if "freeze_start" in line:
            try:
                current_start = float(line.rsplit(":", 1)[-1].strip())
            except (ValueError, IndexError):
                current_start = None
        elif "freeze_end" in line and current_start is not None:
            try:
                end_s = float(line.rsplit(":", 1)[-1].strip())
                freezes.append((current_start, end_s))
            except (ValueError, IndexError):
                pass
            current_start = None
    if current_start is not None:
        # Tail freeze, no matching end — close at clip duration.
        clip_end = float(clip_duration_s or 0) or current_start
        freezes.append((current_start, max(clip_end, current_start)))
    return freezes


async def freeze_detect_video_url(
    video_url: str,
    *,
    clip_duration_s: float,
    provider_name: str,
    block_id: str | None = None,
    render_id: str | None = None,
) -> list[tuple[float, float]]:
    """Download ``video_url`` to a temp file and run freeze detection.

    Returns the list of frozen segments (each >= 0.6 s). On any error
    (download failure, ffmpeg crash) we capture to Sentry and return
    an empty list — a freeze-detect failure must NOT itself fail the
    block. The download timeout scales with clip duration to comply
    with the project rule against hardcoded media timeouts.
    """
    if not video_url:
        return []
    fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="freezechk_")
    os.close(fd)
    try:
        download_timeout = max(60.0, float(clip_duration_s or 0) * 12.0)
        async with httpx.AsyncClient(timeout=download_timeout) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()
            with open(tmp_path, "wb") as fh:
                fh.write(resp.content)
        freezes = await _ffmpeg_freeze_detect(
            tmp_path, clip_duration_s=clip_duration_s,
        )
        if freezes:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag(
                    "freeze_detected_in_bake", provider_name or "unknown"
                )
                if block_id:
                    scope.set_tag("block_id", block_id)
                if render_id:
                    scope.set_tag("render_id", render_id)
                scope.set_extra("freeze_segments", [
                    {"start_s": s, "end_s": e, "duration_s": e - s}
                    for s, e in freezes
                ])
                scope.set_extra(
                    "clip_duration_s", float(clip_duration_s or 0)
                )
                sentry_sdk.capture_message(
                    f"Frozen segment(s) detected in baked clip from "
                    f"{provider_name}",
                    level="warning",
                )
            logger.warning(
                "freeze detected in %s output for block=%s: %s",
                provider_name, block_id, freezes,
            )
        return freezes
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning(
            "freeze_detect_video_url(%s) failed for block=%s: %s",
            provider_name, block_id, exc,
        )
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError as _unlink_exc:
            sentry_sdk.capture_exception(_unlink_exc)


class FrozenBakeError(RuntimeError):
    """Raised by a speaking provider when its baked output contains a
    freeze segment longer than ``_FREEZE_MIN_DURATION_S``. ``try_chain``
    catches it like any other exception and falls through to the next
    tier (which on a top-tier-eligible block is sync-3 — the no-drift
    full-shot engine).
    """

    def __init__(self, provider: str, block_id: str | None,
                 freezes: list[tuple[float, float]]):
        self.provider = provider
        self.block_id = block_id
        self.freezes = freezes
        seg_str = ", ".join(
            f"{s:.2f}-{e:.2f}s" for s, e in freezes
        )
        super().__init__(
            f"frozen segment(s) in {provider} output "
            f"(block={block_id}): {seg_str}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _http_poll_timeout(duration_s: float) -> float:
    """Per-poll wall-clock budget keyed off the input/output media duration.

    HOSTKEY/cloud lipsync ranges from ~3x realtime (MuseTalk) to ~6x realtime
    (InfiniteTalk). 60s constant + 4x duration covers the common case with
    headroom; capped at 1800s (30 min) so a stuck job can't pin a worker.
    """
    return min(60.0 + duration_s * 4.0, 1800.0)


# PR #68: per-duration cloud-tier poll timeout.
#
# The previous cloud provider classes used hardcoded 60–120 s ceilings.
# Wan2.1 InfiniteTalk on WaveSpeed/fal routinely takes 3–8 min per ~10 s
# clip, so those ceilings caused timeouts on every multi-second block in
# render rnd_b2c82c5c90eb. Rule: timeout scales with audio duration; floor
# at 180 s (so tiny clips still have room), cap at 900 s (15 min) so a
# stuck job can't pin the worker forever.
_CLOUD_TIMEOUT_MIN_S = 180
_CLOUD_TIMEOUT_MAX_S = 900
_CLOUD_TIMEOUT_DEFAULT_S = 300  # used when caller didn't pass a duration


def _compute_cloud_timeout(audio_duration_s: float | None) -> int:
    """Return the per-call cloud-tier poll budget for a given audio duration.

    - audio_duration_s ≤ 0 / None → 300 s default (never 60 s / 120 s).
    - 1 s clip   → 180 s (floor).
    - 10 s clip  → 300 s (10 * 30).
    - 30 s clip  → 900 s (capped — would otherwise be 900).
    - 60 s clip  → 900 s (capped).
    """
    try:
        d = float(audio_duration_s or 0)
    except (TypeError, ValueError):
        d = 0.0
    if d <= 0:
        return _CLOUD_TIMEOUT_DEFAULT_S
    return max(_CLOUD_TIMEOUT_MIN_S, min(int(d * 30), _CLOUD_TIMEOUT_MAX_S))


def derive_provider_timeout(
    duration_s: float,
    *,
    floor_s: int = 300,
    ratio: int = 60,
    ceiling_s: int = 1800,
) -> int:
    """Per-block timeout: max(floor, duration * ratio) capped at ceiling.

    Default ratio of 60 means a 10s avatar block gets 600s = 10min, generous
    enough for WaveSpeed cold starts and fal queue backups. Floor of 300s
    keeps tiny blocks (~1-2s) from racing. Ceiling of 1800s (30 min) is the
    hard upper bound — beyond that we'd rather fail and let the dispatcher
    retry the next tier.

    duration_s is the block's effective duration (slot duration, TTS duration,
    or whatever the caller deems "the work size"); caller decides.

    This supersedes the old ``_compute_cloud_timeout`` floor of 180s for
    render/bake/lipsync paths: render rnd_cc4f2d8da674 block 3 (slot 5.00s,
    TTS 9.67s) hit the 180s floor on all three speaking providers because a
    5s segment computed ``int(5 * 30) = 150 < 180`` → exactly the 180s that
    every avatar bake was failing at on cold starts.
    """
    try:
        d = float(duration_s) if duration_s is not None else 0.0
    except (TypeError, ValueError):
        d = 0.0
    return max(floor_s, min(ceiling_s, int(d * ratio)))


HOSTKEY_BASE_URL = os.environ.get("HOSTKEY_GPU_URL", "http://194.247.183.12:7860")

# PR #67 Bug 1: per-render in-memory circuit breaker for HOSTKEY. When
# a render hits a torch OOM on the on-prem GPU, we open the breaker for
# 60 s so subsequent block bakes in the same render skip tier 1 and go
# straight to the cloud fallback. Restart of the on-prem ComfyUI worker
# must be initiated manually by the operator (or by a separate
# cron-style watchdog) — the orchestrator can't safely SSH out.
#
# Shape: {render_id: opened_at_monotonic}.
_HOSTKEY_OOM_BREAKERS: dict[str, float] = {}
_HOSTKEY_OOM_COOLDOWN_S = 60.0
_HOSTKEY_OOM_GC_AFTER_S = 300.0  # drop stale entries after 5 min


def _hostkey_breaker_open(render_id: str | None) -> bool:
    """True iff the breaker for ``render_id`` is currently within the
    cooldown window. Cleans up stale entries while we're here.
    """
    if not render_id:
        return False
    now = time.monotonic()
    # GC stale entries so the dict can't grow without bound.
    stale = [r for r, t in _HOSTKEY_OOM_BREAKERS.items()
             if now - t > _HOSTKEY_OOM_GC_AFTER_S]
    for r in stale:
        _HOSTKEY_OOM_BREAKERS.pop(r, None)
    opened_at = _HOSTKEY_OOM_BREAKERS.get(render_id)
    if opened_at is None:
        return False
    return (now - opened_at) < _HOSTKEY_OOM_COOLDOWN_S


def _hostkey_breaker_open_now(render_id: str | None) -> None:
    """Trip the breaker for ``render_id``. No-op when render_id is empty."""
    if render_id:
        _HOSTKEY_OOM_BREAKERS[render_id] = time.monotonic()


def should_auto_restart_comfyui(error_message: str) -> bool:
    """Return True iff the HOSTKEY error signature indicates a torch OOM
    or CUDA-allocation failure. Used to decide whether to trip the
    per-render circuit breaker and route to the cloud fallback for the
    rest of the render.
    """
    if not error_message:
        return False
    haystack = error_message.lower()
    return (
        "torch.outofmemoryerror" in haystack
        or "outofmemoryerror" in haystack
        or "allocation on device" in haystack
        or "cuda out of memory" in haystack
    )


# ─────────────────────────────────────────────────────────────────────────────
# SPEAKING (lipsync)
# ─────────────────────────────────────────────────────────────────────────────

# VRAM threshold above which InfiniteTalk's ~24 GB peak load won't fit.
# Expressed in MiB to match the on-prem /api/gpu-status response.
_HOSTKEY_VRAM_HIGH_MIB = 18_000


# PR #68: per-render HOSTKEY recovery state.
#
# When the VRAM guard trips three times in the same render, the next
# is_available() call POSTs to the on-prem worker's /api/recover-comfyui
# endpoint (operator deploys it manually — see PR description). The
# orchestrator MUST NOT raise if the endpoint is missing or fails; it
# just marks HOSTKEY unavailable for the rest of this render.
#
# Shape: {render_id: {"high_vram_skips": int, "recovery_attempted": bool,
#                     "recovery_disabled": bool, "last_touch": monotonic}}
_HOSTKEY_RECOVERY_STATE: dict[str, dict] = {}
_HOSTKEY_VRAM_SKIPS_BEFORE_RECOVER = 3
_HOSTKEY_RECOVERY_GC_AFTER_S = 600.0  # drop stale render rows after 10 min


def _hostkey_recovery_row(render_id: str) -> dict:
    """Get-or-create the per-render recovery state row."""
    now = time.monotonic()
    # Best-effort GC so the dict can't grow without bound across many renders.
    stale = [
        r for r, row in _HOSTKEY_RECOVERY_STATE.items()
        if now - row.get("last_touch", 0) > _HOSTKEY_RECOVERY_GC_AFTER_S
    ]
    for r in stale:
        _HOSTKEY_RECOVERY_STATE.pop(r, None)
    row = _HOSTKEY_RECOVERY_STATE.setdefault(
        render_id,
        {"high_vram_skips": 0, "recovery_attempted": False, "recovery_disabled": False},
    )
    row["last_touch"] = now
    return row


def reset_hostkey_recovery_state(render_id: str | None) -> None:
    """Clear the per-render recovery state. Called on render completion
    so we don't carry stale skip counters across runs."""
    if render_id:
        _HOSTKEY_RECOVERY_STATE.pop(render_id, None)


async def _attempt_hostkey_recovery(base_url: str, render_id: str) -> bool:
    """POST /api/recover-comfyui once per render. Return True iff the
    endpoint returned HTTP 200 (i.e. ComfyUI was restarted on the box).

    Defensive: any non-200 status (including 404 when the operator
    hasn't deployed the endpoint yet) or connection error is logged
    and captured to Sentry, but MUST NOT propagate — HOSTKEY simply
    stays unavailable for the rest of this render."""
    url = f"{base_url.rstrip('/')}/api/recover-comfyui"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url)
            if resp.status_code == 200:
                logger.info(
                    "HOSTKEY /api/recover-comfyui succeeded (render=%s): %s",
                    render_id, (resp.text or "")[:200],
                )
                return True
            logger.warning(
                "HOSTKEY /api/recover-comfyui returned %d (render=%s): %s",
                resp.status_code, render_id, (resp.text or "")[:200],
            )
            # Treat 404 (endpoint not yet deployed) and any other non-200
            # the same way: no raise, no retry within this render.
            return False
    except Exception as exc:
        # Designed failover: recovery endpoint missing or unreachable
        # means we leave HOSTKEY skipped for the rest of this render.
        # Breadcrumb keeps the diagnostic without firing a Sentry issue
        # on the normal "endpoint not deployed yet" path.
        sentry_sdk.add_breadcrumb(
            category="render",
            level="info",
            message="hostkey_recovery_endpoint_unreachable",
            data={
                "render_id": render_id,
                "exc_type": type(exc).__name__,
                "exc_str": str(exc)[:200],
            },
        )
        logger.warning(
            "HOSTKEY /api/recover-comfyui POST failed (render=%s): %s",
            render_id, exc,
        )
        return False


class HostkeyInfinitetalkProvider:
    """Tier 1: on-prem GPU server running InfiniteTalk.

    Available iff
      - CAST_RENDER_HOSTKEY_DISABLED != "1"
      - no per-render OOM circuit breaker is open
      - GET /api/gpu-status returns 200 within 5 s
      - memory_used_mib < 18 000 (reject if peak load won't fit)

    PR #67 Bug 1 fix:
      - The previous guard read ``vram_used_gb`` from ``/api/health``,
        but the on-prem worker exposes VRAM only on ``/api/gpu-status``
        as ``memory_used_mib``. The old code therefore always saw
        ``None`` and fell through to "use HOSTKEY anyway" even when the
        GPU was at 18.8 GB.
      - Fails CLOSED: any probe error (timeout, 5xx, JSON parse) marks
        the tier unavailable for this attempt rather than gambling on
        the on-prem worker.
      - On a real OOM during generate(), trips an in-memory circuit
        breaker keyed by ``render_id`` so subsequent block bakes in the
        same render skip tier 1 for 60 s.
    """

    name = "hostkey_infinitetalk"
    tier = 1

    def __init__(self, base_url: str = HOSTKEY_BASE_URL):
        self.base_url = base_url.rstrip("/")

    async def is_available(self, *, render_id: str | None = None, **_kw) -> bool:
        # HOSTKEY is decommissioned: the central kill-switch
        # (CAST_RENDER_HOSTKEY_DISABLED truthy or HOSTKEY_RENDER_ENABLED
        # falsey, both default to "off") short-circuits the on-prem
        # InfiniteTalk tier so try_chain falls through to WaveSpeed
        # InfiniteTalk (tier 2) → fal Hallo (tier 3).
        if hostkey_disabled():
            log_hostkey_skip("wavespeed_infinitetalk")
            sentry_sdk.set_tag("provider_skip_reason", "disabled_env")
            return False
        if _hostkey_breaker_open(render_id):
            logger.info(
                "HOSTKEY skipped: per-render circuit breaker open (render=%s)",
                render_id,
            )
            sentry_sdk.set_tag("provider_skip_reason", "circuit_break_oom")
            return False
        # PR #68: if recovery was attempted for this render and failed
        # (404 from /api/recover-comfyui, connection error, non-200),
        # leave HOSTKEY skipped for the rest of the render — don't
        # gamble on the on-prem worker if the recovery hook never ran.
        if render_id and _HOSTKEY_RECOVERY_STATE.get(render_id, {}).get(
            "recovery_disabled"
        ):
            sentry_sdk.set_tag("provider_skip_reason", "recovery_failed")
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # /api/gpu-status is the canonical VRAM probe on the
                # on-prem worker; /api/health doesn't expose memory.
                resp = await client.get(f"{self.base_url}/api/gpu-status")
                status = resp.status_code
                # 5xx — server is up but broken; skip and let the cloud
                # chain take this render.
                if 500 <= status < 600:
                    sentry_sdk.set_tag("provider_skip_reason", "status_unreachable")
                    return False
                # 404 means the route is missing but the FastAPI app is
                # alive and responding. The orchestrator's VRAM guard
                # can't run without ``memory_used_mib``, but the host
                # itself is reachable — treat as available and let
                # generate() decide. Same for any other non-2xx that
                # isn't a 5xx (e.g. 401/403 if the operator wires auth
                # later) — server is up.
                data: dict = {}
                if 200 <= status < 300:
                    try:
                        data = resp.json()
                    except Exception as exc:
                        sentry_sdk.capture_exception(exc)
                        data = {}
                else:
                    sentry_sdk.add_breadcrumb(
                        category="render",
                        level="info",
                        message="hostkey_gpu_status_non_2xx_but_alive",
                        data={"status_code": status, "render_id": render_id},
                    )
            mib = data.get("memory_used_mib") if isinstance(data, dict) else None
            # Fall back to the legacy vram_used_gb shape if the new
            # endpoint hasn't been deployed yet — convert to MiB.
            if (
                mib is None
                and isinstance(data, dict)
                and isinstance(data.get("vram_used_gb"), (int, float))
            ):
                mib = float(data["vram_used_gb"]) * 1024.0
            if isinstance(mib, (int, float)) and mib >= _HOSTKEY_VRAM_HIGH_MIB:
                logger.info(
                    "HOSTKEY skipped: memory_used_mib=%.0f ≥ %d (too hot)",
                    mib, _HOSTKEY_VRAM_HIGH_MIB,
                )
                sentry_sdk.set_tag("provider_skip_reason", "vram_high")
                # PR #68: per-render recovery watchdog. After observing a
                # high-VRAM skip 3 times we POST /api/recover-comfyui
                # (operator-deployed endpoint on the HOSTKEY box) once
                # per render. If recovery succeeds, HOSTKEY becomes
                # available again on the NEXT call. If it fails (404 /
                # connection error / non-200) we stay unavailable for
                # the rest of this render — no exception raised.
                if render_id:
                    row = _hostkey_recovery_row(render_id)
                    if row.get("recovery_disabled"):
                        return False
                    row["high_vram_skips"] = int(row.get("high_vram_skips", 0)) + 1
                    if (
                        row["high_vram_skips"] >= _HOSTKEY_VRAM_SKIPS_BEFORE_RECOVER
                        and not row.get("recovery_attempted")
                    ):
                        row["recovery_attempted"] = True
                        logger.info(
                            "HOSTKEY VRAM-high skips=%d for render=%s; "
                            "attempting recovery",
                            row["high_vram_skips"], render_id,
                        )
                        ok = await _attempt_hostkey_recovery(
                            self.base_url, render_id,
                        )
                        if ok:
                            # Recovery succeeded; reset counter so the
                            # NEXT is_available() call sees a clean
                            # state and re-probes /api/gpu-status.
                            row["high_vram_skips"] = 0
                        else:
                            # Recovery failed (or endpoint missing).
                            # Skip HOSTKEY for the rest of this render.
                            row["recovery_disabled"] = True
                return False
            # If the worker responded (any non-5xx) but didn't expose
            # memory_used_mib, the host is still alive — treat it as
            # available and let generate() decide. Bug D fix: a 404
            # from the FastAPI app (route missing) used to send us into
            # fail-closed mode and we never tried the on-prem engine.
            return True
        except Exception as exc:
            # Designed failover: an unreachable on-prem status endpoint
            # means HOSTKEY is offline — skip it and let the cloud chain
            # handle this render. Breadcrumb preserves the diagnostic in
            # any subsequent error without raising a standalone issue.
            sentry_sdk.add_breadcrumb(
                category="render",
                level="info",
                message="hostkey_status_unreachable_fallthrough",
                data={
                    "render_id": render_id,
                    "exc_type": type(exc).__name__,
                    "exc_str": str(exc)[:200],
                },
            )
            sentry_sdk.set_tag("provider_skip_reason", "status_unreachable")
            logger.info("HOSTKEY gpu-status probe failed: %s", exc)
            return False

    async def generate(
        self,
        image_url: str,
        audio_url: str,
        prompt: str,
        width: int,
        height: int,
        duration_s: float = 0.0,
        render_id: str | None = None,
        block_id: str | None = None,
        **_kwargs,
    ) -> dict:
        timeout = max(_http_poll_timeout(duration_s), 600.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/infinitetalk-render",
                    json={
                        "image_url": image_url,
                        "wav_url": audio_url,
                        "prompt": prompt,
                        "width": width,
                        "height": height,
                    },
                )
                if resp.status_code == 503:
                    raise RuntimeError("HOSTKEY GPU busy (503)")
                if resp.status_code >= 400:
                    body_preview = (resp.text or "")[:1500]
                    # OOM signatures from ComfyUI / WanVideo bubble up
                    # here as 5xx with a torch.OutOfMemoryError in the
                    # body. Trip the per-render circuit breaker so the
                    # rest of this render goes straight to the cloud
                    # fallback. The actual ComfyUI restart is operator
                    # work — out of scope for this PR.
                    if should_auto_restart_comfyui(body_preview):
                        _hostkey_breaker_open_now(render_id)
                        logger.warning(
                            "HOSTKEY OOM detected (render=%s); opened circuit "
                            "breaker for %.0fs",
                            render_id, _HOSTKEY_OOM_COOLDOWN_S,
                        )
                        sentry_sdk.set_tag("hostkey_oom_breaker_tripped", "1")
                    raise RuntimeError(
                        f"HOSTKEY infinitetalk returned {resp.status_code}: "
                        f"{body_preview}"
                    )
                body = resp.json()
            # Post-bake freeze detection. InfiniteTalk has been observed
            # to stop animating mid-clip on long audio prompts; the
            # baked clip looks fine for the first few seconds, then
            # freezes for the remainder. The check runs on whatever
            # downloadable URL the worker returned — base64-only
            # responses skip the check (no URL to probe). On a freeze
            # hit we raise FrozenBakeError so try_chain falls through
            # to the next tier.
            _vid_url = ""
            if isinstance(body, dict):
                _vid_url = body.get("video_url") or ""
                if not _vid_url and isinstance(body.get("result"), dict):
                    _vid_url = body["result"].get("video_url") or ""
            if _vid_url:
                _freezes = await freeze_detect_video_url(
                    _vid_url,
                    clip_duration_s=duration_s,
                    provider_name=self.name,
                    block_id=block_id,
                    render_id=render_id,
                )
                if _freezes:
                    raise FrozenBakeError(self.name, block_id, _freezes)
            return body
        except Exception as e:
            # Capture before re-raising so try_chain's Sentry tagging
            # has the full stacktrace context.
            sentry_sdk.capture_exception(e)
            raise


def _wavespeed_api_key() -> str:
    """WAVESPEED_API_KEY, preferring the real process env but falling back
    to pydantic settings (which reads .env into the Settings object only —
    it never exports back into os.environ). Without this fallback, every
    render silently skips tier 2 (WaveSpeed InfiniteTalk) as "unavailable"
    even when the key is configured in .env, forcing every block onto the
    much slower/flakier tier-3 fal_hallo provider. See the near-identical
    ``_ensure_wavespeed_key`` in services/render_dispatcher.py, which fixes
    the same gap for the older avatar-generation dispatch path but isn't on
    this (tasks/cast_render.py try_chain) call path.
    """
    key = os.environ.get("WAVESPEED_API_KEY", "")
    if key:
        return key
    try:
        from config import settings
        return settings.WAVESPEED_API_KEY or ""
    except Exception:
        return ""


class WavespeedInfinitetalkProvider:
    """Tier 2: WaveSpeed cloud InfiniteTalk endpoint (~$0.03/s, $0.15 minimum).

    PR #67 Bug 2 fix: the original code sent ``image_url`` /
    ``audio_url`` plus ``width`` / ``height`` which the WaveSpeed API
    rejects with HTTP 400 (``field "audio" is required``). Verified
    via live curl on 2026-05-12 — the correct field names are
    ``image`` / ``audio`` / ``prompt`` / ``seed`` and the resolution
    is a string preset ("480p" / "720p"), not explicit width/height.

    POST   https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk
    Header Authorization: Bearer <WAVESPEED_API_KEY>
    Body   {image, audio, prompt, seed, resolution}
    Response 200: {"data": {"id": "<uuid>", "urls": {"get": "<poll url>"}}}
    Poll   GET https://api.wavespeed.ai/api/v3/predictions/{id}/result
           → data.status in {"created","processing","completed","failed"}
           → data.outputs is the array of result URLs on completion.
    """

    name = "wavespeed_infinitetalk"
    tier = 2
    BASE = "https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk"
    POLL_BASE = "https://api.wavespeed.ai/api/v3/predictions"

    async def is_available(self, **_kw) -> bool:
        return bool(_wavespeed_api_key())

    @staticmethod
    def _resolution_from_dims(width: int, height: int) -> str:
        """Map explicit pixel dims to the WaveSpeed resolution preset.

        WaveSpeed exposes "480p" / "720p" presets rather than free-form
        width/height. The on-prem InfiniteTalk pipeline picks whichever
        preset the cast's quality maps to; we round to the nearest
        supported tier based on the largest dim.
        """
        if max(int(width or 0), int(height or 0)) >= 1080:
            return "720p"
        return "480p"

    async def generate(
        self,
        image_url: str,
        audio_url: str,
        prompt: str,
        width: int,
        height: int,
        duration_s: float = 0.0,
        audio_duration_s: float | None = None,
        block_id: str | None = None,
        render_id: str | None = None,
        **_kwargs,
    ) -> dict:
        api_key = _wavespeed_api_key()
        if not api_key:
            raise RuntimeError("WAVESPEED_API_KEY missing at call time")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "image": image_url,
            "audio": audio_url,
            "prompt": prompt,
            "seed": -1,
            "resolution": self._resolution_from_dims(width, height),
        }
        # Per-block poll budget derived from the block's effective duration
        # (no hardcoded ceiling). Successful blocks on this endpoint have
        # been measured at 600–700 s end-to-end and WaveSpeed cold starts
        # routinely run 4–7 min, so the timeout scales at 60x realtime with
        # a 300 s floor and 1800 s ceiling — see ``derive_provider_timeout``.
        effective_duration = (
            audio_duration_s if audio_duration_s is not None else duration_s
        )
        timeout = float(derive_provider_timeout(effective_duration))
        logger.info(
            "wavespeed poll timeout=%.0fs (duration=%.2fs)",
            timeout, float(effective_duration or 0),
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.BASE, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"WaveSpeed POST {resp.status_code}: {(resp.text or '')[:500]}"
                    )
                body = resp.json()

                data = body.get("data") or body
                outputs = data.get("outputs") or []
                status = data.get("status")
                if status in ("succeeded", "completed") and outputs:
                    video_url = outputs[0] if isinstance(outputs, list) else outputs
                    _freezes = await freeze_detect_video_url(
                        video_url,
                        clip_duration_s=duration_s,
                        provider_name=self.name,
                        block_id=block_id,
                        render_id=render_id,
                    )
                    if _freezes:
                        raise FrozenBakeError(self.name, block_id, _freezes)
                    return {"video_url": video_url, "duration_s": duration_s}

                prediction_id = data.get("id") or body.get("id")
                if not prediction_id:
                    raise RuntimeError(
                        f"WaveSpeed: no prediction id in response: "
                        f"{str(body)[:500]}"
                    )

            # Exponential-then-steady poll backoff (1, 2, 4, 8, 8, 8, …)
            # so a fast job lands inside the first few seconds without
            # hammering the API for slow jobs.
            backoffs = [1.0, 2.0, 4.0, 8.0]
            backoff_idx = 0
            deadline = time.monotonic() + timeout
            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"WaveSpeed prediction {prediction_id} did not "
                            f"complete in {timeout:.0f}s"
                        )
                    poll = await client.get(
                        f"{self.POLL_BASE}/{prediction_id}/result",
                        headers=headers,
                    )
                    if poll.status_code >= 400:
                        raise RuntimeError(
                            f"WaveSpeed poll {poll.status_code}: "
                            f"{(poll.text or '')[:500]}"
                        )
                    pjson = poll.json()
                    pdata = pjson.get("data") or pjson
                    pstatus = pdata.get("status")
                    if pstatus in ("succeeded", "completed"):
                        outputs = pdata.get("outputs") or []
                        if not outputs:
                            raise RuntimeError(
                                f"WaveSpeed prediction {prediction_id} "
                                f"succeeded but returned no outputs"
                            )
                        video_url = outputs[0] if isinstance(outputs, list) else outputs
                        _freezes = await freeze_detect_video_url(
                            video_url,
                            clip_duration_s=duration_s,
                            provider_name=self.name,
                            block_id=block_id,
                            render_id=render_id,
                        )
                        if _freezes:
                            raise FrozenBakeError(
                                self.name, block_id, _freezes,
                            )
                        return {"video_url": video_url, "duration_s": duration_s}
                    if pstatus in ("failed", "canceled", "cancelled", "error"):
                        err = pdata.get("error") or pdata.get("message") or str(pdata)[:200]
                        raise RuntimeError(
                            f"WaveSpeed prediction {prediction_id} {pstatus}: {err}"
                        )
                    sleep_s = backoffs[backoff_idx] if backoff_idx < len(backoffs) else 8.0
                    backoff_idx += 1
                    await asyncio.sleep(sleep_s)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

    @staticmethod
    def _wavespeed_webhook_base() -> str:
        """Same pattern as services.runpod._webhook_base() — a public
        callback URL WaveSpeed can POST to. Falls back to the known prod
        domain when APP_DOMAIN isn't set to something reachable, since a
        webhook to localhost is never deliverable."""
        from config import settings
        domain = getattr(settings, "APP_DOMAIN", None) or "localhost"
        if domain in ("localhost", "127.0.0.1"):
            return "https://www.luminacast.com/api/webhooks/wavespeed"
        scheme = "http" if domain.replace(".", "").isdigit() else "https"
        return f"{scheme}://{domain}/api/webhooks/wavespeed"

    async def submit_webhook(
        self,
        image_url: str,
        audio_url: str,
        prompt: str,
        width: int,
        height: int,
        job_tag: str,
    ) -> str:
        """Submit-and-return variant of ``generate()`` — fires the job with
        a webhook callback instead of polling, so the calling worker is
        free immediately. Mirrors RunPodService.submit_video_job_webhook.

        ``job_tag`` identifies the caller (e.g. ``f"avatar_{avatar_id}"``)
        so the webhook receiver can match the completed job back to the
        right record — WaveSpeed doesn't accept arbitrary metadata on the
        request, so the tag is only used by the caller to store/match the
        returned prediction id, not sent to WaveSpeed itself.
        """
        api_key = _wavespeed_api_key()
        if not api_key:
            raise RuntimeError("WAVESPEED_API_KEY missing at call time")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "image": image_url,
            "audio": audio_url,
            "prompt": prompt,
            "seed": -1,
            "resolution": self._resolution_from_dims(width, height),
        }
        webhook_url = self._wavespeed_webhook_base()
        submit_url = f"{self.BASE}?webhook={webhook_url}"

        logger.info(
            "wavespeed webhook submit job_tag=%s webhook=%s", job_tag, webhook_url,
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(submit_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"WaveSpeed POST {resp.status_code}: {(resp.text or '')[:500]}"
                )
            body = resp.json()

        data = body.get("data") or body
        prediction_id = data.get("id") or body.get("id")
        if not prediction_id:
            raise RuntimeError(
                f"WaveSpeed webhook submit: no prediction id in response: {str(body)[:500]}"
            )
        logger.info(
            "wavespeed webhook job submitted prediction_id=%s job_tag=%s",
            prediction_id, job_tag,
        )
        return prediction_id


class FalHalloProvider:
    """Tier 3: fal.ai Hallo image-driven talking-portrait fallback.

    PR #67 Bug 3 fix: the previous tier 3 used ``fal-ai/musetalk`` which
    requires a ``source_video_url`` (a real video clip), not an image —
    so every call from our image+audio call site landed at 400. Verified
    via live curl on 2026-05-12 that ``fal-ai/hallo`` accepted an
    ``image_url`` + ``audio_url`` pair.

    That field has since been renamed upstream: fal's current OpenAPI schema
    for this endpoint (checked live) requires ``source_image_url``, not
    ``image_url``. The submit call was still accepted (200) because fal's
    queue API only validates the payload against the real schema when the
    job actually runs — the failure only surfaced as a 422 on the *result*
    fetch, well after submission looked successful. This tier was silently
    100% broken until that rename was caught.

    Submit:
        POST https://queue.fal.run/fal-ai/hallo
        body {source_image_url, audio_url}
        → 200 {request_id, status_url, response_url}
    Poll:
        GET <status_url>           → {status: IN_QUEUE|IN_PROGRESS|COMPLETED|...}
        GET <response_url>         → {video: {url}}      (on COMPLETED)
    """

    name = "fal_hallo"
    tier = 3
    ENDPOINT = "fal-ai/hallo"
    SUBMIT_BASE = "https://queue.fal.run"

    async def is_available(self, **_kw) -> bool:
        return bool(os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"))

    @staticmethod
    def _auth_header() -> dict[str, str]:
        key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or ""
        return {
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        image_url: str,
        audio_url: str,
        duration_s: float = 0.0,
        audio_duration_s: float | None = None,
        block_id: str | None = None,
        render_id: str | None = None,
        **_kwargs,
    ) -> dict:
        headers = self._auth_header()
        if not headers["Authorization"].endswith(" "):
            pass  # key present
        else:
            raise RuntimeError("FAL_KEY / FAL_API_KEY missing at call time")

        submit_url = f"{self.SUBMIT_BASE}/{self.ENDPOINT}"
        payload = {"source_image_url": image_url, "audio_url": audio_url}

        # Per-block poll budget derived from the block's effective duration
        # (no hardcoded ceiling). A cold fal queue can sit a multi-second
        # clip in IN_QUEUE for minutes; the timeout scales at 60x realtime
        # with a 300 s floor and 1800 s ceiling — see
        # ``derive_provider_timeout``.
        effective_duration = (
            audio_duration_s if audio_duration_s is not None else duration_s
        )
        timeout = float(derive_provider_timeout(effective_duration, floor_s=90))
        logger.info(
            "%s poll timeout=%.0fs (effective_duration=%.2fs, source=%s)",
            self.name, timeout, float(effective_duration or 0),
            "audio_duration_s" if audio_duration_s is not None else "duration_s",
        )
        backoffs = [2.0, 4.0, 8.0]
        backoff_idx = 0

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                submit = await client.post(submit_url, headers=headers, json=payload)
                if submit.status_code >= 400:
                    raise RuntimeError(
                        f"fal submit {submit.status_code}: "
                        f"{(submit.text or '')[:500]}"
                    )
                sub_body = submit.json()
                status_url = sub_body.get("status_url")
                response_url = sub_body.get("response_url")
                if not status_url or not response_url:
                    raise RuntimeError(
                        f"fal submit body missing status_url/response_url: "
                        f"{str(sub_body)[:300]}"
                    )

            deadline = time.monotonic() + timeout
            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"fal hallo did not complete in {timeout:.0f}s"
                        )
                    status_resp = await client.get(status_url, headers=headers)
                    if status_resp.status_code >= 400:
                        raise RuntimeError(
                            f"fal status {status_resp.status_code}: "
                            f"{(status_resp.text or '')[:500]}"
                        )
                    sjson = status_resp.json()
                    sstatus = (sjson.get("status") or "").upper()
                    if sstatus == "COMPLETED":
                        result_resp = await client.get(response_url, headers=headers)
                        if result_resp.status_code >= 400:
                            raise RuntimeError(
                                f"fal result {result_resp.status_code}: "
                                f"{(result_resp.text or '')[:500]}"
                            )
                        body = result_resp.json()
                        video = body.get("video") or {}
                        video_url = video.get("url") if isinstance(video, dict) else None
                        if not video_url:
                            raise RuntimeError(
                                f"fal hallo returned no video url: "
                                f"{str(body)[:300]}"
                            )
                        _freezes = await freeze_detect_video_url(
                            video_url,
                            clip_duration_s=duration_s,
                            provider_name=self.name,
                            block_id=block_id,
                            render_id=render_id,
                        )
                        if _freezes:
                            raise FrozenBakeError(
                                self.name, block_id, _freezes,
                            )
                        return {"video_url": video_url, "duration_s": duration_s}
                    if sstatus in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
                        raise RuntimeError(
                            f"fal hallo {sstatus}: {str(sjson)[:300]}"
                        )
                    sleep_s = backoffs[backoff_idx] if backoff_idx < len(backoffs) else 8.0
                    backoff_idx += 1
                    await asyncio.sleep(sleep_s)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise


# Backwards-compat alias — the old class name still resolves so external
# code that pinned to ``FalMusetalkProvider`` keeps working until callers
# are migrated. Logs will show ``fal_hallo`` (the new ``name``).
FalMusetalkProvider = FalHalloProvider


# Kling V3 Pro Elements bakes a product-conditioned base video. The lipsync
# stage downstream still runs on the standard speaking chain to add mouth
# sync against this base; this provider does NOT produce a final lipsynced
# clip on its own. Output: 720p portrait video, h264, audio disabled.
#
# Endpoint: fal-ai/kling-video/v3/pro/image-to-video with the Elements
# feature engaged via the elements[] array. Kling's max clip length is
# 5 s per segment — longer blocks are baked in chunks and stitched.
#
# We deliberately do NOT name the engine vendor anywhere a user-visible
# string can land: status labels, error messages bubbled to the UI, and
# block-status provider columns all use the internal name
# ``product_elements_bake`` rather than ``kling_v3_pro``. The vendor name
# lives in this file and the structured log lines only.
_KLING_ELEMENTS_MAX_SEGMENT_S = 5.0
_KLING_ELEMENTS_SUBMIT_BASE = "https://queue.fal.run"
_KLING_ELEMENTS_ENDPOINT = "fal-ai/kling-video/v3/pro/image-to-video"

# Kling Elements ``duration`` is validated server-side as a literal of
# integer-strings in ['3'..'14']. Anything outside that window — including
# floats, sub-3s remainders, or values >14 — returns HTTP 422 before the
# bake even starts. See render rnd_27326258cd64 (block 5/10, 11.10s) where
# the previous 5+5+1.10 chunking blew up at the 1.10s tail. We chunk in
# the integer domain and clamp every segment to [3, 14].
_KLING_ELEMENTS_MIN_SEG_S = 3
_KLING_ELEMENTS_MAX_SEG_S = 14


def _plan_kling_segments(total_seconds: float) -> list[int]:
    """Plan Kling Elements segment durations for a block.

    Kling Elements only accepts integer-second durations in [3, 14]; the
    caller (provider chain) is expected to fall through to the next tier
    when this returns an empty list.

    Returns:
        - ``[]`` when ``total_seconds < 3`` — Kling cannot serve sub-3s clips.
        - ``[ceil(total)]`` (capped at 14) for 3 <= total <= 14.
        - For total > 14: a list of ints each in [3, 14], summing to roughly
          ``ceil(total)``. The last segment is never < 3 — we steal from
          the preceding chunk to keep that invariant.

    Examples:
        1.5  -> []
        2.5  -> []
        4.5  -> [5]
        11.10 -> [7, 5] (chunk=7 then 4 remainder, but 4>=3 so kept)
        16.5 -> [7, 10]  (rebalanced so last >=3)
        22   -> [7, 7, 8]
    """
    if total_seconds is None or total_seconds < _KLING_ELEMENTS_MIN_SEG_S:
        return []
    total = int(math.ceil(float(total_seconds)))
    if total <= _KLING_ELEMENTS_MAX_SEG_S:
        # Single segment; clamp into [3, 14].
        return [max(_KLING_ELEMENTS_MIN_SEG_S, min(total, _KLING_ELEMENTS_MAX_SEG_S))]
    # total > 14: emit 7s chunks until what's left fits a final segment.
    # 7s sits in the preferred 5-8s quality window and divides 14 evenly,
    # so we never split a clip into a chunk-of-1 trailing piece.
    chunk = 7
    segs: list[int] = []
    remaining = total
    while remaining > _KLING_ELEMENTS_MAX_SEG_S:
        segs.append(chunk)
        remaining -= chunk
    # remaining is now in [1, 14] after the loop. Must be >=3 to be a valid
    # final segment; if it isn't, steal from the previous chunk. deficit is
    # at most 2 (3 - 1), so the donor (was 7) stays well above the 3s floor.
    if remaining < _KLING_ELEMENTS_MIN_SEG_S:
        deficit = _KLING_ELEMENTS_MIN_SEG_S - remaining
        if segs:
            segs[-1] -= deficit
            remaining = _KLING_ELEMENTS_MIN_SEG_S
        else:
            # Unreachable for total>14, but defensive.
            remaining = _KLING_ELEMENTS_MIN_SEG_S
    segs.append(remaining)
    # Final safety clamp — every segment in [3, 14].
    segs = [max(_KLING_ELEMENTS_MIN_SEG_S, min(_KLING_ELEMENTS_MAX_SEG_S, s)) for s in segs]
    # Invariant: no segment is below the Kling minimum or above the maximum.
    assert all(_KLING_ELEMENTS_MIN_SEG_S <= s <= _KLING_ELEMENTS_MAX_SEG_S for s in segs), segs
    return segs


class KlingV3ProElementsProvider:
    """Product-conditioned speaking-block bake.

    Use when a speaking block has an associated product reference image and
    the block script references the product by name. Routed in ahead of the
    standard speaking chain so the resulting video carries the actual
    uploaded product in the avatar's hand (instead of the lipsync engines
    hallucinating a generic bottle from face_ref + noise alone).

    Inputs (via generate kwargs):
        image_url:           avatar face_ref — both the segment's start
                             frame AND the elements ``frontal_image_url``
                             (the SUBJECT). Per the fal Kling-V3-Pro
                             Elements docs, ``frontal_image_url`` is the
                             subject's front-facing photo, not the
                             object being referenced.
        product_image_url:   single product reference image — appended
                             to ``reference_image_urls`` (legacy single
                             form, kept for backwards-compat).
        product_image_urls:  list of product reference images —
                             populates ``reference_image_urls`` directly,
                             capped at 4 entries to satisfy the API.
        prompt:              speaking-scene prompt (already references the
                             product via the ``@product1`` token)
        duration_s:          target clip length; chunked into ≤5 s segments
        width, height:       canvas dims; output is 720p portrait

    Output:
        {"video_url": str, "duration_s": float, "segments": int}

    The lipsync chain then consumes ``video_url`` as the base video to add
    mouth sync against. This provider does NOT produce a lipsynced clip on
    its own — it only conditions the base motion on the product image.

    Cost: ~$1.12 per 5 s clip (Elements active, audio off).

    Per-call poll timeout scales with duration via ``derive_provider_timeout``
    — never a hardcoded ceiling, so a slow Kling job doesn't surface a
    TimeoutError on every multi-second clip.
    """

    name = "product_elements_bake"
    tier = 1

    async def is_available(self, **_kw) -> bool:
        return bool(os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"))

    @staticmethod
    def _auth_header() -> dict[str, str]:
        key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or ""
        return {
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
        }

    async def _bake_segment(
        self,
        *,
        image_url: str,
        product_image_urls: list[str],
        prompt: str,
        segment_duration_s: float,
        client: httpx.AsyncClient,
        timeout_s: float,
        start_image_url: str | None = None,
    ) -> str:
        """Submit one ≤5 s segment to the elements endpoint and return its
        public video URL on success. Raises on any error.

        Payload shape (per fal Kling-V3-Pro Elements docs):
          - ``frontal_image_url``: the SUBJECT's front-facing photo —
            the avatar's face_ref image
          - ``reference_image_urls``: non-empty list of OBJECT / STYLE
            references — the product cover image(s) to incorporate
            into the scene

        Earlier code put the product image in ``frontal_image_url`` and
        sent an empty ``reference_image_urls`` list, which the endpoint
        rejects with HTTP 422 "Either frontal_image_url and
        reference_image_urls or video_url must be provided" (observed
        on render rnd_24578b0444ce).
        """
        headers = self._auth_header()
        if not image_url:
            raise RuntimeError(
                "product-elements: frontal subject image_url is required"
            )
        refs = [u for u in (product_image_urls or []) if u]
        if not refs:
            raise RuntimeError(
                "product-elements: at least one product reference image url "
                "is required"
            )
        # Cap reference list at 4 — Kling Elements rejects >4 refs.
        refs = refs[:4]
        # Kling validates ``duration`` as an integer-string literal in
        # ['3'..'14']. The caller is expected to pass an already-planned
        # integer-second value from ``_plan_kling_segments``; we clamp
        # here defensively in case a stray float slips through.
        seg_int = int(round(float(segment_duration_s)))
        seg_int = max(_KLING_ELEMENTS_MIN_SEG_S, min(seg_int, _KLING_ELEMENTS_MAX_SEG_S))
        dur_str = str(seg_int)
        # When a pre-generated scene still is supplied (Option A: FLUX
        # Kontext scene already shows the real product correctly held), it
        # becomes the I2V start frame so the motion preserves the product
        # identity from frame one. The avatar face_ref stays as the
        # Elements ``frontal_image_url`` subject anchor.
        seg_start_image = start_image_url or image_url
        payload = {
            "start_image_url": seg_start_image,
            "prompt": prompt,
            "elements": [
                {
                    "frontal_image_url": image_url,
                    "reference_image_urls": refs,
                }
            ],
            "duration": dur_str,
            "generate_audio": False,
            "negative_prompt": (
                "blur, distort, low quality, wrong product, "
                "different bottle, split bottle, warped label"
            ),
        }
        submit_url = f"{_KLING_ELEMENTS_SUBMIT_BASE}/{_KLING_ELEMENTS_ENDPOINT}"
        submit = await client.post(submit_url, headers=headers, json=payload)
        if submit.status_code >= 400:
            raise RuntimeError(
                f"product-elements submit {submit.status_code}: "
                f"{(submit.text or '')[:500]}"
            )
        sub_body = submit.json()
        status_url = sub_body.get("status_url")
        response_url = sub_body.get("response_url")
        if not status_url or not response_url:
            raise RuntimeError(
                f"product-elements submit body missing status_url/response_url: "
                f"{str(sub_body)[:300]}"
            )

        deadline = time.monotonic() + timeout_s
        backoffs = [2.0, 4.0, 8.0]
        backoff_idx = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"product-elements segment did not complete in {timeout_s:.0f}s"
                )
            status_resp = await client.get(status_url, headers=headers)
            if status_resp.status_code >= 400:
                raise RuntimeError(
                    f"product-elements status {status_resp.status_code}: "
                    f"{(status_resp.text or '')[:500]}"
                )
            sjson = status_resp.json()
            sstatus = (sjson.get("status") or "").upper()
            if sstatus == "COMPLETED":
                result_resp = await client.get(response_url, headers=headers)
                if result_resp.status_code >= 400:
                    raise RuntimeError(
                        f"product-elements result {result_resp.status_code}: "
                        f"{(result_resp.text or '')[:500]}"
                    )
                body = result_resp.json()
                video = body.get("video") or {}
                video_url = (
                    video.get("url") if isinstance(video, dict) else None
                )
                if not video_url:
                    # Some fal shapes inline the video URL at the top level.
                    video_url = body.get("video_url")
                if not video_url:
                    raise RuntimeError(
                        f"product-elements returned no video url: "
                        f"{str(body)[:300]}"
                    )
                return video_url
            if sstatus in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
                raise RuntimeError(
                    f"product-elements {sstatus}: {str(sjson)[:300]}"
                )
            sleep_s = backoffs[backoff_idx] if backoff_idx < len(backoffs) else 8.0
            backoff_idx += 1
            await asyncio.sleep(sleep_s)

    async def generate(
        self,
        image_url: str,
        prompt: str,
        audio_url: str | None = None,
        product_image_url: str | None = None,
        product_image_urls: list[str] | None = None,
        script_references_product: bool = False,
        duration_s: float = 0.0,
        audio_duration_s: float | None = None,
        width: int = 720,
        height: int = 1280,
        scene_start_image_url: str | None = None,
        **_kwargs,
    ) -> dict:
        # Accept either a single product_image_url (legacy) or a list
        # of product_image_urls. Both are normalised into a single
        # reference list before submission.
        refs: list[str] = []
        if product_image_urls:
            refs.extend([u for u in product_image_urls if u])
        if product_image_url and product_image_url not in refs:
            refs.append(product_image_url)
        if not refs or not script_references_product:
            # Defensive: provider should only be inserted when the caller
            # has at least one product image AND the block script
            # references the product. Raising here turns a programming
            # error into a chain-fallthrough rather than a silent bad
            # bake.
            raise RuntimeError(
                "product-elements provider requires at least one product "
                "reference image and script_references_product=True"
            )

        effective_duration = (
            audio_duration_s if audio_duration_s is not None else duration_s
        )
        try:
            total_s = float(effective_duration or 0)
        except (TypeError, ValueError):
            total_s = 0.0
        if total_s <= 0:
            total_s = _KLING_ELEMENTS_MAX_SEGMENT_S

        # Plan integer-second segments per Kling Elements' validation:
        # each segment in [3, 14]; sub-3s totals skip Kling entirely so
        # the dispatcher can fall through to the next provider. The
        # planner returns an empty list for total < 3s.
        segments: list[int] = _plan_kling_segments(total_s)
        logger.info(
            "Kling Elements segment plan for %.2fs: %s", total_s, segments,
        )
        if not segments:
            # Sub-3s block: Kling refuses any duration below 3 with a
            # 422 literal_error. Surface as a RuntimeError so the
            # provider chain falls through to the next tier (the
            # dispatcher catches RuntimeError on each provider try).
            raise RuntimeError(
                "kling-elements: block too short (<3s), falling through"
            )

        # Each planned segment is sized to fit Kling's per-call ceiling,
        # so we size the per-segment poll timeout off the longest planned
        # chunk rather than the legacy 5s constant. Derived per-segment with
        # a 300 s floor / 1800 s ceiling so a 5 s segment no longer races
        # the old 180 s floor on a cold Kling job.
        longest_seg = float(max(segments))
        per_segment_timeout = float(derive_provider_timeout(longest_seg))
        logger.info(
            "product-elements bake: total=%.2fs, segments=%d, per-segment timeout=%.0fs",
            total_s, len(segments), per_segment_timeout,
        )

        try:
            segment_urls: list[str] = []
            async with httpx.AsyncClient(timeout=30.0) as client:
                for i, seg_dur in enumerate(segments):
                    logger.info(
                        "product-elements segment %d/%d (%.2fs)",
                        i + 1, len(segments), seg_dur,
                    )
                    url = await self._bake_segment(
                        image_url=image_url,
                        product_image_urls=refs,
                        prompt=prompt,
                        segment_duration_s=seg_dur,
                        client=client,
                        timeout_s=per_segment_timeout,
                        start_image_url=scene_start_image_url,
                    )
                    probed_dur = await _probe_remote_duration_s(url)
                    logger.info(
                        "product-elements segment %d/%d: downloaded url=%s, "
                        "requested=%.2fs probed=%.2fs",
                        i + 1, len(segments), url[:120], seg_dur, probed_dur,
                    )
                    if probed_dur > 0 and probed_dur < seg_dur - 0.5:
                        # Segment came back shorter than requested — log loudly
                        # so the freeze-vs-short-segment ambiguity is visible
                        # at triage time. We don't raise; the stitch+ping-pong
                        # downstream handles short clips, but Sentry should
                        # know this happened.
                        anom = RuntimeError(
                            f"product-elements segment {i+1}/{len(segments)} "
                            f"short: requested {seg_dur:.2f}s, "
                            f"probed {probed_dur:.2f}s"
                        )
                        sentry_sdk.capture_exception(anom)
                    segment_urls.append(url)

            total_requested = float(sum(segments))
            if len(segment_urls) == 1:
                base_video_url = segment_urls[0]
                segment_count = 1
                final_dur = await _probe_remote_duration_s(base_video_url)
                logger.info(
                    "product-elements single segment: duration=%.2fs "
                    "(expected %.2fs)",
                    final_dur, total_requested,
                )
            else:
                base_video_url = await _stitch_segment_urls(
                    segment_urls, total_duration_s=total_requested,
                )
                segment_count = len(segment_urls)
                final_dur = await _probe_remote_duration_s(base_video_url)
                logger.info(
                    "product-elements stitched output: duration=%.2fs "
                    "(expected %.2fs, %d segments)",
                    final_dur, total_requested, segment_count,
                )
                if final_dur > 0 and final_dur < total_requested - 0.5:
                    anom = RuntimeError(
                        f"product-elements stitch short: requested "
                        f"{total_requested:.2f}s, got {final_dur:.2f}s "
                        f"from {segment_count} segments"
                    )
                    sentry_sdk.capture_exception(anom)

            # Add mouth-sync over the product-aware base via the cloud
            # lipsync stage. Without this step the avatar's mouth is the
            # generic motion Kling produced and won't match the audio.
            if not audio_url:
                # No audio means no lipsync needed (silent block); ship
                # the base video as-is. Downstream mux still attaches a
                # silent track when needed.
                return {
                    "video_url": base_video_url,
                    "duration_s": total_s,
                    "segments": segment_count,
                    "product_conditioned": True,
                    "lipsynced": False,
                }

            from services.kling_lipsync import apply_lipsync
            lipsync_result = await apply_lipsync(
                video_url=base_video_url,
                audio_url=audio_url,
            )
            final_url = lipsync_result.get("video_url")
            if not final_url:
                raise RuntimeError(
                    "product-elements lipsync stage returned no video_url"
                )
            return {
                "video_url": final_url,
                "duration_s": total_s,
                "segments": segment_count,
                "product_conditioned": True,
                "lipsynced": True,
                "base_video_url": base_video_url,
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise


async def _probe_remote_duration_s(url: str) -> float:
    """ffprobe a remote video URL and return its container duration in
    seconds. Returns 0.0 when probing fails (network, no ffprobe, weird
    container) — the caller treats 0 as "unknown" and continues.

    Used by the product-elements diagnostics path so we can log the
    actual duration of each segment / stitched output and catch the
    "two 5s segments stitched to 5.11s" symptom at the source instead
    of only noticing it in the normalize pad log downstream.
    """
    if not url:
        return 0.0
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return 0.0
        return float((proc.stdout or "0").strip() or 0.0)
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError) as e:
        sentry_sdk.capture_exception(e)
        return 0.0


async def _stitch_segment_urls(
    segment_urls: list[str], total_duration_s: float = 0.0,
) -> str:
    """Download each segment, ffmpeg-concat them, upload the joined mp4 to
    R2, and return its public URL.

    The concat is performed via the demuxer (``-f concat -safe 0``) so we
    avoid a full re-encode when the segments share the same codec params
    (they do — all come from the same Kling endpoint). On any per-segment
    download or ffmpeg error we capture to Sentry and re-raise so the
    chain falls through to the standard speaking providers.

    ``total_duration_s`` is the summed planned duration of the segments; the
    ffmpeg concat deadline is derived from it (no hardcoded constant) so a
    long multi-segment stitch isn't capped at a fixed wall-clock budget.
    """
    import subprocess
    import tempfile
    from services.r2_storage import get_r2_storage_service

    r2 = get_r2_storage_service()
    tmp_dir = tempfile.mkdtemp(prefix="prod_elements_")
    seg_paths: list[str] = []
    out_path = os.path.join(tmp_dir, "stitched.mp4")
    list_path = os.path.join(tmp_dir, "concat.txt")

    # Per-segment download budget scales with the planned output duration
    # (60s floor + 12x realtime) so a long stitch isn't capped at a fixed
    # 120 s — matches the freeze-detect download scaling elsewhere in this
    # module.
    download_timeout = max(120.0, float(total_duration_s or 0) * 12.0)
    try:
        async with httpx.AsyncClient(timeout=download_timeout) as client:
            for i, url in enumerate(segment_urls):
                seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.mp4")
                resp = await client.get(url)
                resp.raise_for_status()
                with open(seg_path, "wb") as fh:
                    fh.write(resp.content)
                # Probe each downloaded segment so we can pinpoint which one
                # is short before the demuxer-copy concat silently drops it.
                try:
                    probe = await asyncio.to_thread(
                        subprocess.run,
                        [
                            "ffprobe", "-v", "quiet",
                            "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1",
                            seg_path,
                        ],
                        capture_output=True, text=True, timeout=30,
                    )
                    seg_local_dur = float((probe.stdout or "0").strip() or 0.0)
                except (subprocess.TimeoutExpired, ValueError) as e:
                    sentry_sdk.capture_exception(e)
                    seg_local_dur = 0.0
                logger.info(
                    "product-elements stitch: segment %d/%d downloaded "
                    "(%d bytes, probed=%.2fs)",
                    i + 1, len(segment_urls), len(resp.content), seg_local_dur,
                )
                seg_paths.append(seg_path)

        with open(list_path, "w") as fh:
            for p in seg_paths:
                fh.write(f"file '{p}'\n")

        # Stream-copy concat (no re-encode). If the segments don't share
        # codec params, ffmpeg will raise and the caller falls through to
        # the standard speaking chain — no silent corrupt mp4 ever ships.
        # Deadline derived from the planned output duration (60s floor +
        # 8x realtime) rather than a hardcoded 180s — a long multi-segment
        # stitch must not be capped at a fixed wall-clock budget.
        concat_timeout = max(60.0, float(total_duration_s or 0) * 8.0)
        proc = await asyncio.to_thread(
            subprocess.run,
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                out_path,
            ],
            capture_output=True,
            text=True,
            timeout=concat_timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"product-elements stitch ffmpeg failed: "
                f"{(proc.stderr or '')[:500]}"
            )

        with open(out_path, "rb") as fh:
            stitched_bytes = fh.read()
        if not stitched_bytes:
            raise RuntimeError("product-elements stitch produced empty mp4")

        try:
            probe = await asyncio.to_thread(
                subprocess.run,
                [
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    out_path,
                ],
                capture_output=True, text=True, timeout=30,
            )
            stitched_dur = float((probe.stdout or "0").strip() or 0.0)
        except (subprocess.TimeoutExpired, ValueError) as e:
            sentry_sdk.capture_exception(e)
            stitched_dur = 0.0
        logger.info(
            "product-elements stitch: %d segments → stitched.mp4 "
            "(%d bytes, probed=%.2fs)",
            len(segment_urls), len(stitched_bytes), stitched_dur,
        )

        output_key = f"renders/product_elements/{int(time.time())}_{len(segment_urls)}seg.mp4"
        await r2.upload_bytes(stitched_bytes, output_key, content_type="video/mp4")
        return r2.get_public_url(output_key)
    finally:
        # Best-effort cleanup. Disk pressure is unlikely but a leaking
        # /tmp eventually breaks the worker, so we sweep on every call.
        try:
            for p in seg_paths + [list_path, out_path]:
                if os.path.exists(p):
                    os.unlink(p)
            os.rmdir(tmp_dir)
        except OSError as e:
            sentry_sdk.capture_exception(e)


# ─────────────────────────────────────────────────────────────────────────────
# fal-ai sync-lipsync (v3 + v2/pro) — top-tier lipsync (PR #82)
# ─────────────────────────────────────────────────────────────────────────────
#
# Sync Labs' hosted lipsync, accessible via fal's queue API. Two model
# variants:
#
#   v3       — "sync-3", full-shot processing (no rolling-window
#              segment boundaries), 4K-capable. $8/min. Best quality;
#              the only engine in the chain that does not exhibit the
#              81-frame boundary drift observed on rolling-window
#              encoders like WaveSpeed InfiniteTalk.
#   v2/pro   — chunk-based with diffusion super-res on the face region.
#              $5/min. Still chunked (2s independent inference windows)
#              so micro-drift across chunk boundaries can be present;
#              use as a price-tier fallback before falling back to the
#              on-prem / WaveSpeed cascade.
#
# Both endpoints take video_url + audio_url. They do NOT take an image
# (use FalHalloProvider for image+audio). The call site in cast_render
# only routes the chain here when there's a real video clip to drive
# off of — for the initial bake from a face image we still need the
# rolling-window engines.
#
# Submit:
#   POST https://queue.fal.run/<endpoint>
#   body {video_url, audio_url}
#   → 200 {request_id, status_url, response_url}
# Poll:
#   GET <status_url>           → {status: IN_QUEUE|IN_PROGRESS|COMPLETED|...}
#   GET <response_url>         → {video: {url}}      (on COMPLETED)


class _FalSyncLipsyncBase:
    """Shared submit/poll loop for the two sync-lipsync model variants.

    Subclasses set ``name``, ``tier``, ``ENDPOINT`` and ``BASE_COST``.
    The lipsync caller passes ``video_url`` + ``audio_url`` (the two
    inputs the model takes) and we return ``{"video_url": ...,
    "duration_s": ...}`` in the same shape as the other speaking
    providers so downstream extraction stays uniform.
    """

    SUBMIT_BASE = "https://queue.fal.run"
    ENDPOINT: str = ""

    async def is_available(self, **_kw) -> bool:
        return bool(os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"))

    @staticmethod
    def _auth_header() -> dict[str, str]:
        key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or ""
        return {
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
        }

    async def refine(
        self,
        video_url: str,
        audio_url: str,
        *,
        duration_s: float = 0.0,
        block_id: str | None = None,
        render_id: str | None = None,
    ) -> dict:
        """Post-bake mouth-motion re-sync entry point.

        Thin wrapper around ``generate`` that documents the intent
        (refining an existing video bake) and routes the kwargs the
        same way the post-bake call site uses. Returns the same
        ``{"video_url": ..., "duration_s": ...}`` shape.
        """
        return await self.generate(
            video_url=video_url,
            audio_url=audio_url,
            duration_s=duration_s,
            audio_duration_s=duration_s,
            block_id=block_id,
            render_id=render_id,
        )

    async def generate(
        self,
        video_url: str | None = None,
        audio_url: str | None = None,
        duration_s: float = 0.0,
        audio_duration_s: float | None = None,
        sync_mode: str | None = None,
        **_kwargs,
    ) -> dict:
        # sync-lipsync v3 / v2-pro require an actual video as
        # ``video_url`` (mp4/mov/avi). Earlier code forwarded
        # ``image_url`` here when no video was supplied, which the
        # endpoint rejects with HTTP 422 "Unsupported video format" —
        # observed on render rnd_24578b0444ce where face_ref.jpg was
        # being posted as video_url. Refusing here keeps the failure
        # visible at the call site instead of round-tripping a 422.
        if not (os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")):
            raise RuntimeError("FAL_KEY / FAL_API_KEY missing at call time")
        if not video_url:
            raise RuntimeError(
                f"{self.name}: video_url (an existing baked video) is required"
            )
        if not audio_url:
            raise RuntimeError(f"{self.name}: audio_url is required")
        headers = self._auth_header()
        driving_url = video_url

        submit_url = f"{self.SUBMIT_BASE}/{self.ENDPOINT}"
        payload: dict = {"video_url": driving_url, "audio_url": audio_url}
        # sync_mode controls how the engine reconciles a video/audio
        # length mismatch ("loop" repeats a short driving video to cover
        # longer audio — used by the PIP still-loop fallback).
        if sync_mode:
            payload["sync_mode"] = sync_mode

        # Per-block poll budget derived from the block's effective duration,
        # identical formula to FalHallo / WaveSpeed (300 s floor, 1800 s
        # ceiling, 60x realtime — see ``derive_provider_timeout``). A cold
        # fal sync-lipsync queue can exceed the old 180 s floor on a short
        # clip even though today's PIP render lucked into a 57 s completion.
        effective_duration = (
            audio_duration_s if audio_duration_s is not None else duration_s
        )
        timeout = float(derive_provider_timeout(effective_duration, floor_s=90))
        logger.info(
            "%s poll timeout=%.0fs (effective_duration=%.2fs, source=%s)",
            self.name, timeout, float(effective_duration or 0),
            "audio_duration_s" if audio_duration_s is not None else "duration_s",
        )
        backoffs = [2.0, 4.0, 8.0]
        backoff_idx = 0

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                submit = await client.post(submit_url, headers=headers, json=payload)
                if submit.status_code >= 400:
                    raise RuntimeError(
                        f"{self.name} submit {submit.status_code}: "
                        f"{(submit.text or '')[:500]}"
                    )
                sub_body = submit.json()
                fal_request_id = sub_body.get("request_id") or ""
                status_url = sub_body.get("status_url")
                response_url = sub_body.get("response_url")
                if not status_url or not response_url:
                    raise RuntimeError(
                        f"{self.name} submit body missing status_url/response_url: "
                        f"{str(sub_body)[:300]}"
                    )

            deadline = time.monotonic() + timeout
            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"{self.name} did not complete in {timeout:.0f}s"
                        )
                    status_resp = await client.get(status_url, headers=headers)
                    if status_resp.status_code >= 400:
                        raise RuntimeError(
                            f"{self.name} status {status_resp.status_code}: "
                            f"{(status_resp.text or '')[:500]}"
                        )
                    sjson = status_resp.json()
                    sstatus = (sjson.get("status") or "").upper()
                    if sstatus == "COMPLETED":
                        result_resp = await client.get(response_url, headers=headers)
                        if result_resp.status_code >= 400:
                            raise RuntimeError(
                                f"{self.name} result {result_resp.status_code}: "
                                f"{(result_resp.text or '')[:500]}"
                            )
                        body = result_resp.json()
                        video = body.get("video") or {}
                        out_url = video.get("url") if isinstance(video, dict) else None
                        # Some fal endpoints return the URL at the top
                        # level instead of nested under "video".
                        if not out_url:
                            out_url = body.get("video_url") or body.get("url")
                        if not out_url:
                            raise RuntimeError(
                                f"{self.name} returned no video url: "
                                f"{str(body)[:300]}"
                            )
                        return {
                            "video_url": out_url,
                            "duration_s": duration_s,
                            "fal_request_id": fal_request_id,
                        }
                    if sstatus in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
                        raise RuntimeError(
                            f"{self.name} {sstatus}: {str(sjson)[:300]}"
                        )
                    sleep_s = backoffs[backoff_idx] if backoff_idx < len(backoffs) else 8.0
                    backoff_idx += 1
                    await asyncio.sleep(sleep_s)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise


class FalSyncLipsyncV3Provider(_FalSyncLipsyncBase):
    """Tier 0 (when applicable): fal-ai/sync-lipsync/v3 — best lipsync
    quality available in the fal catalogue at the time of PR #82.

    Full-shot processing means no 81-frame / 2-second segment
    boundaries, which were the root cause of the observed drift at
    0:13-0:20 and 0:33-0:43 in rnd_… renders against WaveSpeed
    InfiniteTalk. Cost is roughly $8/min — the provider_chain only
    routes to this tier for blocks where the cost is justified
    (duration >= 10s or PRODUCTION_LIPSYNC=1).

    Tier 0 (lower than the existing on-prem tier 1) so that when
    ``build_speaking_chain`` includes us, ``try_chain``'s tier-sort
    keeps us strictly ahead of HOSTKEY.
    """

    name = "fal_sync_lipsync_v3"
    tier = 0
    ENDPOINT = "fal-ai/sync-lipsync/v3"


class FalSyncLipsyncV2ProProvider(_FalSyncLipsyncBase):
    """Tier 0 (when applicable): fal-ai/sync-lipsync/v2/pro — chunked
    diffusion lipsync with face-region super-res. ~$5/min.

    Used as the immediate fallback when v3 is unavailable or fails.
    Chunk-boundary micro-drift is possible but the quality is still
    well above the on-prem / WaveSpeed tier for blocks where v3 isn't
    cost-justified.

    Tier 0 (alongside v3) so both top-tier providers sit ahead of
    HOSTKEY in the speaking chain; the list insertion order (v3 first,
    v2-pro second — see ``build_speaking_chain``) is preserved by
    Python's stable sort.
    """

    name = "fal_sync_lipsync_v2_pro"
    tier = 0
    ENDPOINT = "fal-ai/sync-lipsync/v2/pro"


# ─────────────────────────────────────────────────────────────────────────────
# PIP fallback: looped-still face → fal sync-lipsync
# ─────────────────────────────────────────────────────────────────────────────
#
# PIP (talking-head) blocks have no driving video — only a face image and
# audio. The on-prem MuseTalk path and the broken RunPod InfiniteTalk
# template were the only routes for them, so when both are unavailable a
# PIP block hard-fails (observed on render rnd_17ed66313c9a → block
# blk_c59218e232f4). sync-lipsync needs an actual video as video_url, so
# we synthesise one by looping the face image into a still video of the
# required duration, then drive fal sync-lipsync v2/pro off of it. The
# audio still animates the mouth; the rest of the frame is held — visually
# fine for a small PIP inset.


async def _loop_face_to_still_video(
    face_ref_url: str,
    *,
    block_id: str,
    duration_s: float,
    width: int,
    height: int,
    fps: int = 25,
) -> str:
    """Download a face image, loop it into a still ``duration_s`` mp4 at
    ``width``x``height``, upload to R2 under ``tmp/pip_face_loops/`` and
    return a signed URL fal can fetch.

    The ffmpeg timeout scales with clip duration (60s floor + 8x realtime)
    rather than a hardcoded ceiling, per the project rule against hardcoded
    media deadlines — encoding a still loop is far faster than realtime so
    this is generous headroom.
    """
    dur = float(duration_s or 0)
    if dur <= 0:
        raise RuntimeError(
            f"_loop_face_to_still_video: duration_s must be > 0 (block={block_id})"
        )
    if not face_ref_url:
        raise RuntimeError(
            f"_loop_face_to_still_video: face_ref_url is required (block={block_id})"
        )

    fd_img, img_path = tempfile.mkstemp(suffix=".jpg", prefix="pipface_")
    os.close(fd_img)
    fd_vid, vid_path = tempfile.mkstemp(suffix=".mp4", prefix="piploop_")
    os.close(fd_vid)
    try:
        download_timeout = max(60.0, dur * 12.0)
        async with httpx.AsyncClient(timeout=download_timeout) as client:
            resp = await client.get(face_ref_url)
            resp.raise_for_status()
            with open(img_path, "wb") as fh:
                fh.write(resp.content)

        encode_timeout = max(60.0, dur * 8.0)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-loop", "1", "-i", img_path,
            "-t", f"{dur:.3f}",
            "-r", str(fps),
            "-vf", f"scale={width}:{height}",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-tune", "stillimage",
            vid_path,
        ]
        proc = await asyncio.to_thread(
            subprocess.run, cmd,
            capture_output=True, timeout=encode_timeout, check=False,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", "replace")
            raise RuntimeError(
                f"ffmpeg still-loop failed (block={block_id}): {stderr[:500]}"
            )

        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        key = f"tmp/pip_face_loops/{block_id}.mp4"
        await r2.upload_file(vid_path, key, content_type="video/mp4")
        # Signed URL: this is a server-to-server fetch by fal, not
        # browser-facing, and the tmp/ object is not on the public CDN
        # path. Expiry must outlast the lipsync poll budget so the URL stays
        # valid for the whole job — derive it from the same per-block timeout
        # the fal sync-lipsync provider uses (plus a 300 s buffer).
        expires_in = int(derive_provider_timeout(dur)) + 300
        return r2.get_signed_url(key, expires_in=expires_in)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise
    finally:
        for p in (img_path, vid_path):
            try:
                os.unlink(p)
            except OSError as _unlink_exc:
                sentry_sdk.capture_exception(_unlink_exc)


async def pip_lipsync_via_fal(
    *,
    face_ref_url: str,
    audio_url: str,
    block_id: str,
    duration_s: float,
    width: int,
    height: int,
) -> dict:
    """PIP fallback: loop ``face_ref_url`` into a still video, then run fal
    sync-lipsync v2/pro to animate the mouth from ``audio_url``.

    Returns ``{"output": {"video_url": <url>}, "backend":
    "fal_sync_lipsync_v2_pro"}`` so the dispatcher's downstream extraction
    (which reads ``output["video_url"]``) handles it unchanged.

    v2/pro is chosen over v3 here because it is faster and cheaper, and a
    PIP inset does not need v3's full-shot drift-free quality. ``sync_mode``
    is set to ``loop`` so the lipsync engine loops the (short) still video
    to match the audio length rather than truncating.
    """
    loop_video_url = await _loop_face_to_still_video(
        face_ref_url,
        block_id=block_id,
        duration_s=duration_s,
        width=width,
        height=height,
    )
    provider = FalSyncLipsyncV2ProProvider()
    result = await provider.generate(
        video_url=loop_video_url,
        audio_url=audio_url,
        duration_s=float(duration_s or 0),
        audio_duration_s=float(duration_s or 0),
        sync_mode="loop",
    )
    out_url = result.get("video_url")
    if not out_url:
        raise RuntimeError(
            f"pip_lipsync_via_fal: no video_url returned (block={block_id})"
        )
    return {"output": {"video_url": out_url}, "backend": "fal_sync_lipsync_v2_pro"}


# ─────────────────────────────────────────────────────────────────────────────
# TTS (text → voice-cloned audio)
# ─────────────────────────────────────────────────────────────────────────────
#
# Tier 1 today is the in-process FishAudioService, which itself cascades
# HOSTKEY → RunPod Fish Speech → Fish Audio Cloud. We expose it here as
# a single tier-1 provider so the chain remains uniform; the cloud-only
# Fish Audio path is also exposed as tier 2 for the case where the local
# fish_audio service can't reach any of its backends.

class FishAudioProvider:
    """Tier 1: existing FishAudioService (HOSTKEY → RunPod cascade inside).

    Reuses services.fish_audio.get_fish_audio_service so the well-tested
    sanitization, RunPod retries, and R2 upload path are preserved.
    is_available is True iff the service has any backend wired up — at the
    very least Fish Audio Cloud (FISH_AUDIO_API_KEY).
    """

    name = "hostkey_fish_speech"
    tier = 1

    async def is_available(self, **_kw) -> bool:
        try:
            from config import settings
            return bool(
                getattr(settings, "FISH_AUDIO_API_KEY", "")
                or getattr(settings, "FISH_SPEECH_ENDPOINT_ID", "")
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            return False

    async def generate(self, text: str, voice_id: str, **kwargs) -> dict:
        from services.fish_audio import get_fish_audio_service
        fish = get_fish_audio_service()
        return await fish.generate_tts(
            text=text,
            voice_id=voice_id,
            clip_mic_enabled=bool(kwargs.get("clip_mic_enabled", False)),
            scene_chain_id=kwargs.get("scene_chain_id"),
            block_id=kwargs.get("block_id"),
        )


class FishAudioCloudProvider:
    """Tier 2: Fish Audio public API (https://api.fish.audio/v1/tts).

    Calls the cloud endpoint directly with the user's reference voice. The
    voice_id may be either a Fish Audio UUID (use as `reference_id`) or an
    R2 key (load bytes and pass under `references`). Returns the standard
    {audio_key, duration_seconds, tmp_path} dict so callers don't branch.
    """

    name = "fish_audio_cloud"
    tier = 2
    BASE = "https://api.fish.audio/v1/tts"

    async def is_available(self, **_kw) -> bool:
        return bool(os.environ.get("FISH_AUDIO_API_KEY"))

    async def generate(
        self,
        text: str,
        voice_id: str,
        duration_hint_s: float = 0.0,
        **_kwargs,
    ) -> dict:
        import tempfile
        import subprocess

        api_key = os.environ.get("FISH_AUDIO_API_KEY", "")
        if not api_key:
            raise RuntimeError("FISH_AUDIO_API_KEY missing at call time")

        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()

        is_r2_voice = "/" in voice_id or voice_id.endswith(".wav")
        payload: dict = {
            "text": text,
            "format": "mp3",
        }
        if is_r2_voice:
            tmp_ref = tempfile.mktemp(suffix=".wav")
            try:
                await r2.download_file(voice_id, tmp_ref)
                with open(tmp_ref, "rb") as fh:
                    ref_b64 = base64.b64encode(fh.read()).decode("ascii")
            finally:
                try:
                    os.unlink(tmp_ref)
                except OSError:
                    pass
            payload["references"] = [{"audio": ref_b64, "text": ""}]
        else:
            payload["reference_id"] = voice_id

        timeout = max(_http_poll_timeout(duration_hint_s), 120.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                self.BASE,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            audio_bytes = resp.content

        if not audio_bytes:
            raise RuntimeError("Fish Audio cloud returned empty audio body")

        output_key = f"tts/{voice_id.replace('/', '_')}/{int(time.time())}.mp3"
        await r2.upload_bytes(audio_bytes, output_key, content_type="audio/mpeg")

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(audio_bytes)

        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            duration_seconds = float(proc.stdout.strip()) if proc.stdout.strip() else 0.0
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            duration_seconds = round(len(audio_bytes) / 16000.0, 2)

        result = {
            "audio_key": output_key,
            "duration_seconds": round(duration_seconds, 2),
            "tmp_path": tmp_path,
        }
        # Broadcast-quality post-processing — produces a 44.1 kHz MP3
        # master (overwrites audio_key) and a sibling 16 kHz WAV
        # (lipsync_audio_key). On failure we leave the raw upload in
        # place rather than failing the chain.
        try:
            from services.voice_postprocess_upload import post_process_and_upload
            mix_key, lipsync_key = await post_process_and_upload(
                r2=r2,
                raw_tmp_path=tmp_path,
                mix_r2_key=output_key,
                clip_mic_enabled=bool(_kwargs.get("clip_mic_enabled", False)),
                scene_chain_id=_kwargs.get("scene_chain_id"),
                block_id=_kwargs.get("block_id"),
            )
            result["audio_key"] = mix_key
            result["lipsync_audio_key"] = lipsync_key
        except Exception as pp_exc:
            sentry_sdk.capture_exception(pp_exc)
        return result


class ElevenLabsTTSProvider:
    """Tier 3: ElevenLabs as last-resort TTS for emergencies.

    Note: ElevenLabs voice IDs are NOT interchangeable with Fish Audio voice
    IDs, so this provider is only useful when the cast's avatar has an
    ElevenLabs voice configured (voice_id != Fish UUID/R2 key). If the
    voice_id isn't ElevenLabs-formatted we mark unavailable so the chain
    surfaces a real "all providers failed" error instead of a 401.
    """

    name = "elevenlabs_tts"
    tier = 3

    async def is_available(self, **_kw) -> bool:
        try:
            from config import settings
            return bool(getattr(settings, "ELEVENLABS_API_KEY", ""))
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            return False

    async def generate(
        self,
        text: str,
        voice_id: str,
        duration_hint_s: float = 0.0,
        **_kwargs,
    ) -> dict:
        import tempfile
        import subprocess
        from config import settings
        from services.r2_storage import get_r2_storage_service

        api_key = getattr(settings, "ELEVENLABS_API_KEY", "")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY missing at call time")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        timeout = max(_http_poll_timeout(duration_hint_s), 120.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={"text": text, "model_id": "eleven_turbo_v2_5"},
            )
            resp.raise_for_status()
            audio_bytes = resp.content

        if not audio_bytes:
            raise RuntimeError("ElevenLabs returned empty audio body")

        r2 = get_r2_storage_service()
        output_key = f"tts/elevenlabs/{voice_id}/{int(time.time())}.mp3"
        await r2.upload_bytes(audio_bytes, output_key, content_type="audio/mpeg")

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(audio_bytes)
        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            duration_seconds = float(proc.stdout.strip()) if proc.stdout.strip() else 0.0
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            duration_seconds = round(len(audio_bytes) / 16000.0, 2)
        result = {
            "audio_key": output_key,
            "duration_seconds": round(duration_seconds, 2),
            "tmp_path": tmp_path,
        }
        # Broadcast-quality post-processing (see FishAudioCloudProvider).
        try:
            from services.voice_postprocess_upload import post_process_and_upload
            mix_key, lipsync_key = await post_process_and_upload(
                r2=r2,
                raw_tmp_path=tmp_path,
                mix_r2_key=output_key,
                clip_mic_enabled=bool(_kwargs.get("clip_mic_enabled", False)),
                scene_chain_id=_kwargs.get("scene_chain_id"),
                block_id=_kwargs.get("block_id"),
            )
            result["audio_key"] = mix_key
            result["lipsync_audio_key"] = lipsync_key
        except Exception as pp_exc:
            sentry_sdk.capture_exception(pp_exc)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# TRANSCRIPTION (audio → segments + word_timestamps)
# ─────────────────────────────────────────────────────────────────────────────

class HostkeyWhisperxProvider:
    """Tier 1: HOSTKEY WhisperX via /api/whisperx-transcribe (or /api/whisper-transcribe).

    Uses the existing GPUServerClient.whisper_transcribe so we honour all of
    its segment / word flattening conventions.
    """

    name = "hostkey_whisperx"
    tier = 1

    async def is_available(self, **_kw) -> bool:
        # HOSTKEY WhisperX is retired; fall through to fal Whisper (tier 2).
        if hostkey_disabled():
            log_hostkey_skip("fal_whisper")
            return False
        try:
            from services.gpu_server import get_gpu_server_client
            client = get_gpu_server_client()
            if client is None:
                return False
            return await client.is_healthy()
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            return False

    async def generate(
        self,
        audio_url: str,
        language: str = "en",
        word_timestamps: bool = True,
        **_kwargs,
    ) -> dict:
        from services.gpu_server import get_gpu_server_client
        client = get_gpu_server_client()
        if client is None:
            raise RuntimeError("GPU server client not configured")
        return await client.whisper_transcribe(
            audio_url=audio_url,
            language=language,
            word_timestamps=word_timestamps,
        )


class FalWhisperProvider:
    """Tier 2: fal-ai/whisper for transcription with word timestamps."""

    name = "fal_whisper"
    tier = 2
    ENDPOINT = "fal-ai/whisper"

    async def is_available(self, **_kw) -> bool:
        return bool(os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"))

    async def generate(
        self,
        audio_url: str,
        language: str = "en",
        word_timestamps: bool = True,
        **_kwargs,
    ) -> dict:
        if not os.environ.get("FAL_KEY"):
            # docker-compose always injects a FAL_KEY env var (empty string
            # when unset, via ${FAL_KEY:-}) since only FAL_API_KEY is ever
            # set in .env — an `is None` check here never sees that as
            # missing, so this fallback silently never ran and fal_client
            # authenticated with an empty key. `not x` catches empty string
            # too, matching every other FAL_KEY bridge in this codebase.
            from config import settings as _settings
            if os.environ.get("FAL_API_KEY"):
                os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
            elif _settings.FAL_API_KEY:
                os.environ["FAL_KEY"] = _settings.FAL_API_KEY
        import fal_client

        arguments = {
            "audio_url": audio_url,
            "task": "transcribe",
            "language": language,
        }
        if word_timestamps:
            arguments["chunk_level"] = "word"

        def _run():
            return fal_client.subscribe(self.ENDPOINT, arguments=arguments)

        result = await asyncio.to_thread(_run)

        # fal Whisper returns: {"text": str, "chunks": [{timestamp: [s,e], text}], ...}
        # Normalize to the GPUServerClient.whisper_transcribe schema:
        #   {"transcript", "duration_seconds", "segments": [...], "words": [...]}
        chunks = result.get("chunks") or []
        words: list[dict] = []
        segments: list[dict] = []
        for chunk in chunks:
            ts = chunk.get("timestamp") or [0, 0]
            start = float(ts[0]) if ts and ts[0] is not None else 0.0
            end = float(ts[1]) if len(ts) > 1 and ts[1] is not None else start
            text = (chunk.get("text") or "").strip()
            if not text:
                continue
            if word_timestamps and len(text.split()) <= 1:
                words.append({"word": text, "start": start, "end": end, "probability": 1.0})
            else:
                segments.append({"start": start, "end": end, "text": text})

        if word_timestamps and not words and segments:
            for seg in segments:
                seg_words = (seg.get("text") or "").split()
                if not seg_words:
                    continue
                step = (seg["end"] - seg["start"]) / max(len(seg_words), 1)
                for i, w in enumerate(seg_words):
                    words.append({
                        "word": w,
                        "start": round(seg["start"] + i * step, 3),
                        "end": round(seg["start"] + (i + 1) * step, 3),
                        "probability": 0.5,
                    })

        return {
            "transcript": result.get("text", ""),
            "duration_seconds": (
                float(segments[-1]["end"]) if segments else
                (float(words[-1]["end"]) if words else 0.0)
            ),
            "segments": segments,
            "words": words,
        }


async def transcribe_audio(audio_url: str, language: str = "en") -> dict:
    """Transcribe a single audio URL via the fal Whisper tier.

    Thin wrapper used by the live-reference pipeline (one chunk at a time).
    Returns ``{"text": str, "segments": [{text, start, end}, ...],
    "duration_seconds": float}`` — the chunk's own timeline (offsets applied
    by the caller when stitching).
    """
    result = await FalWhisperProvider().generate(
        audio_url=audio_url,
        language=language,
        word_timestamps=False,
    )
    segments = [
        {"text": s.get("text", ""), "start": float(s.get("start", 0.0)), "end": float(s.get("end", 0.0))}
        for s in (result.get("segments") or [])
    ]
    return {
        "text": result.get("transcript", ""),
        "segments": segments,
        "duration_seconds": float(result.get("duration_seconds", 0.0) or 0.0),
    }


__all__ = [
    "HostkeyInfinitetalkProvider",
    "WavespeedInfinitetalkProvider",
    "FalHalloProvider",
    "FalMusetalkProvider",  # backwards-compat alias for FalHalloProvider
    "KlingV3ProElementsProvider",
    # Top-tier full-shot lipsync (no segment-boundary drift).
    "FalSyncLipsyncV3Provider",
    "FalSyncLipsyncV2ProProvider",
    "FishAudioProvider",
    "FishAudioCloudProvider",
    "ElevenLabsTTSProvider",
    "HostkeyWhisperxProvider",
    "FalWhisperProvider",
    "transcribe_audio",
    # PR #67 helpers exposed for tests / external circuit-break callers.
    "should_auto_restart_comfyui",
    # PR #68 helpers.
    "_compute_cloud_timeout",
    # Duration-derived per-block timeout for render/bake/lipsync providers.
    "derive_provider_timeout",
    "reset_hostkey_recovery_state",
]
