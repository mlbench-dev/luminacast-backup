"""Multi-tier provider cascade with structured Sentry capture.

Usage:
    from services.provider_chain import try_chain, AllProvidersFailedError
    from services.render_providers import (
        HostkeyInfinitetalkProvider,
        WavespeedInfinitetalkProvider,
        FalHalloProvider,
    )

    result = await try_chain(
        [HostkeyInfinitetalkProvider(), WavespeedInfinitetalkProvider(), FalHalloProvider()],
        step_label="speaking",
        render_id=render_id,
        block_id=block_id,
        image_url=face_url, audio_url=audio_url, prompt=prompt,
        width=w, height=h, duration_s=duration_s,
    )

    # result includes vendor-specific keys plus _provider_used and _tier_used
    provider = result["_provider_used"]
    tier = result["_tier_used"]

Behavior:
  - Sorts the provided list by tier ascending (cheapest first).
  - Skips providers whose is_available() is False (logged at INFO).
  - Catches *every* exception from generate(...), routes it to sentry with
    {provider, tier, step, render_id, block_id} tags, and falls through.
  - If a tier-1 attempt failed but a later tier succeeded, logs a "recovered"
    line so it's easy to grep how often we needed the fallback.
  - Raises AllProvidersFailedError if every provider either is_available=False
    or raised. The caller decides how to surface the failure to users.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import sentry_sdk

logger = logging.getLogger(__name__)


# sync-lipsync v3 / v2-pro are no longer inserted into the first-pass
# speaking chain — they require an existing video to re-sync mouth
# motion against, and the first pass is the bake that produces that
# video. The post-bake refinement step in cast_render runs them after
# a successful bake when the block is long enough to be worth the
# $5-8/min cost. Threshold lives there alongside the call site.
_SYNC_LIPSYNC_REFINE_MIN_DURATION_S = 3.0

# When SPEAKING_LIPSYNC_REFINE_FOR_ALL is on we still skip blocks below
# a tiny floor: a sub-half-second block has no perceptible mouth motion
# to correct and the round-trip cost/latency isn't justified.
_SPEAKING_LIPSYNC_REFINE_FLOOR_S = 0.5

# Default block types that get the post-bake refine when motion refine
# is enabled. Motion bakes (text/image→video) invent mouth motion that
# does not match the TTS muxed in later, so they are the primary drift
# source the refine pass corrects.
_MOTION_LIPSYNC_REFINE_DEFAULT_TYPES = (
    "HOOK",
    "PRODUCT",
    "PRODUCT_DEMO",
    "SOCIAL_PROOF",
    "PIP",
)
# Cost guard: a motion clip longer than this is expensive to re-sync and
# the perceptual payoff drops, so it is skipped by default.
_MOTION_LIPSYNC_REFINE_DEFAULT_MAX_DURATION_S = 20.0


@dataclass(frozen=True)
class RefineDecision:
    """Structured outcome of a lipsync-refine gate.

    ``run`` is the verdict; ``reason`` is a short, grep-able token that
    explains *why* (for the per-block diagnostic log line). ``route`` is
    ``motion`` or ``speak`` so a single log format covers both gates.
    """

    run: bool
    reason: str
    route: str


def _production_lipsync_enabled() -> bool:
    """True iff the env flag is set to force the post-bake sync-lipsync
    refine step on every speaking block, regardless of duration. The
    flag is read at refinement-decision time so it can be flipped
    without a process restart in staging."""
    return os.environ.get("PRODUCTION_LIPSYNC", "") == "1"


def _speaking_refine_for_all_enabled() -> bool:
    """True iff SPEAKING_LIPSYNC_REFINE_FOR_ALL is set. When on, every
    speaking block above the tiny floor is refined regardless of the
    legacy >=3s duration gate — the user is paying for quality and the
    old gate let short speak blocks (@0:19, @0:47) drift uncorrected."""
    return os.environ.get("SPEAKING_LIPSYNC_REFINE_FOR_ALL", "") == "1"


def _motion_refine_enabled() -> bool:
    """Master switch for the motion-block post-bake refine. Defaults to
    ON: motion bakes are the dominant mouth-vs-voice drift source, so
    the safe default is to correct them. Set
    MOTION_LIPSYNC_REFINE_ENABLED=0 to disable."""
    return os.environ.get("MOTION_LIPSYNC_REFINE_ENABLED", "1") != "0"


def _motion_refine_block_types() -> frozenset[str]:
    """The set of block types eligible for motion refine. Comma-separated
    env override; falls back to the default tuple. Upper-cased so the
    comparison is case-insensitive against the block's type token."""
    raw = os.environ.get("MOTION_LIPSYNC_REFINE_BLOCK_TYPES", "") or ""
    if raw.strip():
        parsed = {t.strip().upper() for t in raw.split(",") if t.strip()}
        if parsed:
            return frozenset(parsed)
    return frozenset(_MOTION_LIPSYNC_REFINE_DEFAULT_TYPES)


def _motion_refine_max_duration_s() -> float:
    """Cost-guard ceiling for motion refine. Env override; falls back to
    the default. A non-positive value disables the ceiling."""
    raw = os.environ.get("MOTION_LIPSYNC_REFINE_MAX_DURATION_S", "")
    try:
        if str(raw).strip():
            return float(raw)
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
    return _MOTION_LIPSYNC_REFINE_DEFAULT_MAX_DURATION_S


def should_run_motion_lipsync_refine(
    block_type: str | None,
    block_duration_s: float,
) -> RefineDecision:
    """Decision gate for the post-bake refine on a *motion* block.

    Motion blocks (HOOK / PRODUCT / PRODUCT_DEMO / SOCIAL_PROOF / PIP)
    bake via text/image→video models that invent mouth motion unrelated
    to the TTS audio muxed in afterwards. Feeding the baked clip + TTS
    through sync-lipsync rewrites the lower face to match the voice.

    Gating order (each returns a distinct ``reason`` token):
      1. master switch off            → ``motion_disabled``
      2. block type not in allow-list → ``type_not_eligible``
      3. duration over the ceiling    → ``over_max_duration``
      4. otherwise                    → ``eligible``
    """
    if not _motion_refine_enabled():
        return RefineDecision(False, "motion_disabled", "motion")
    btype = (block_type or "").strip().upper()
    if btype not in _motion_refine_block_types():
        return RefineDecision(False, "type_not_eligible", "motion")
    try:
        dur = float(block_duration_s or 0)
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        dur = 0.0
    ceiling = _motion_refine_max_duration_s()
    if ceiling > 0 and dur > ceiling:
        return RefineDecision(False, "over_max_duration", "motion")
    return RefineDecision(True, "eligible", "motion")


def evaluate_speaking_lipsync_refine(block_duration_s: float) -> RefineDecision:
    """Decision gate for the post-bake refine on a *speaking* block.

    Gating order (each returns a distinct ``reason`` token):
      1. SPEAKING_LIPSYNC_REFINE_FOR_ALL=1 → refine every block above a
         tiny floor (``speak_all_above_floor`` / ``below_floor``). This
         removes the legacy >=3s duration gate that let short speak
         blocks drift uncorrected (user complaints @0:19, @0:47).
      2. PRODUCTION_LIPSYNC=1               → ``production_forced``
      3. duration >= 3s                     → ``duration_ge_min``
      4. otherwise                          → ``below_min_duration``
    """
    try:
        dur = float(block_duration_s or 0)
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        dur = 0.0

    if _speaking_refine_for_all_enabled():
        if dur >= _SPEAKING_LIPSYNC_REFINE_FLOOR_S:
            return RefineDecision(True, "speak_all_above_floor", "speak")
        return RefineDecision(False, "below_floor", "speak")

    if _production_lipsync_enabled():
        return RefineDecision(True, "production_forced", "speak")

    if dur >= _SYNC_LIPSYNC_REFINE_MIN_DURATION_S:
        return RefineDecision(True, "duration_ge_min", "speak")
    return RefineDecision(False, "below_min_duration", "speak")


def should_run_sync_lipsync_refine(block_duration_s: float) -> bool:
    """Boolean façade over :func:`evaluate_speaking_lipsync_refine` kept
    for existing call sites that only need the verdict."""
    return evaluate_speaking_lipsync_refine(block_duration_s).run


def build_speaking_chain(
    *,
    block_duration_s: float,
    hostkey_acquired: bool,
):
    """Return the ordered list of speaking-step providers for one block.

    The first-pass chain produces a baked video clip from a still face
    image + audio. Only video-generating providers belong here:
    on-prem InfiniteTalk → WaveSpeed InfiniteTalk → fal Hallo.

    sync-lipsync v3 / v2-pro are NOT first-pass generators — they
    re-sync mouth motion on an existing video. They are wired
    separately as a post-bake refinement step in the caller
    (see ``cast_render._refine_with_sync_lipsync``), running over the
    output of whichever provider in this chain succeeded.

    ``hostkey_acquired=False`` removes the on-prem InfiniteTalk
    provider so we don't double-book the local GPU when a sibling
    bake is already on it.

    The chain is sorted by ``tier`` inside ``try_chain``, so the
    relative tier numbers on the providers themselves dictate the
    real order — this helper just decides which providers belong in
    the list for this block.
    """
    from services.render_providers import (
        HostkeyInfinitetalkProvider,
        WavespeedInfinitetalkProvider,
        FalHalloProvider,
    )

    providers: list = []
    if hostkey_acquired:
        providers.append(HostkeyInfinitetalkProvider())
    providers.append(WavespeedInfinitetalkProvider())
    providers.append(FalHalloProvider())
    return providers


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in a chain either was unavailable or failed.

    Carries the full per-provider error log so the caller can include it in
    the per-render error metadata for debugging.
    """

    def __init__(self, step_label: str, errors: list[dict]):
        self.step_label = step_label
        self.errors = errors
        msg_lines = [f"All providers failed for step={step_label!r}:"]
        for entry in errors:
            msg_lines.append(
                f"  - {entry.get('provider')} (tier {entry.get('tier')}): "
                f"{entry.get('status')} {entry.get('error') or ''}".rstrip()
            )
        super().__init__("\n".join(msg_lines))


async def try_chain(
    providers: list,
    step_label: str,
    render_id: str | None = None,
    block_id: str | None = None,
    on_attempt: Any = None,
    **kwargs: Any,
) -> dict:
    """Walk providers in tier order, return the first success, raise if all fail.

    Every exception is captured to Sentry with provider/tier/step/render_id/
    block_id tags. The successful result dict is returned with _provider_used
    and _tier_used keys merged in.

    `on_attempt`, if given, is an async callback invoked as
    `await on_attempt({"phase": "started"|"failed", "provider": ..., "tier": ...})`
    right before a provider's generate() call and again on its failure — lets
    the caller persist live progress (which tier is being tried right now)
    instead of the caller only finding out after every tier has been
    exhausted. A raising callback is swallowed so a status-write hiccup can
    never affect the actual generation attempt.
    """
    if not providers:
        raise AllProvidersFailedError(step_label, errors=[])

    sorted_providers = sorted(providers, key=lambda p: getattr(p, "tier", 99))

    error_log: list[dict] = []
    tier1_failed = False

    for provider in sorted_providers:
        name = getattr(provider, "name", provider.__class__.__name__)
        tier = getattr(provider, "tier", 99)

        try:
            # PR #67 Bug 1: HOSTKEY's per-render OOM circuit breaker
            # needs render_id at availability-check time. We forward
            # render_id + block_id via **kwargs; providers ignore
            # unknown kwargs via **_kw.
            available = await provider.is_available(
                render_id=render_id, block_id=block_id,
            )
        except TypeError:
            # Older provider classes that don't accept the kwargs.
            try:
                available = await provider.is_available()
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                available = False
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("provider %s is_available() raised: %s — skipping", name, exc)
            error_log.append({
                "provider": name,
                "tier": tier,
                "status": "unavailable",
                "error": f"is_available raised: {exc}",
            })
            if tier == 1:
                tier1_failed = True
            continue

        if not available:
            logger.info("provider %s unavailable, skipping", name)
            error_log.append({
                "provider": name,
                "tier": tier,
                "status": "unavailable",
                "error": None,
            })
            if tier == 1:
                tier1_failed = True
            continue

        started = time.monotonic()
        # PR #67: forward render_id/block_id into generate() kwargs so
        # providers can scope side-effects (HOSTKEY OOM circuit breaker,
        # per-render rate limiting, logging) to the right render. Don't
        # clobber values the caller already passed via **kwargs.
        generate_kwargs = dict(kwargs)
        generate_kwargs.setdefault("render_id", render_id)
        generate_kwargs.setdefault("block_id", block_id)
        if on_attempt:
            try:
                await on_attempt({"phase": "started", "provider": name, "tier": tier})
            except Exception:
                pass
        try:
            result = await provider.generate(**generate_kwargs)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("provider", name)
                scope.set_tag("tier", str(tier))
                scope.set_tag("step", step_label)
                if render_id:
                    scope.set_tag("render_id", render_id)
                if block_id:
                    scope.set_tag("block_id", block_id)
                scope.set_extra("provider_chain_step", step_label)
                scope.set_extra("provider_chain_kwargs_keys", sorted(kwargs.keys()))
                sentry_sdk.capture_exception(exc)
            logger.warning(
                "provider %s tier %d failed for %s: %s",
                name, tier, step_label, exc,
            )
            error_log.append({
                "provider": name,
                "tier": tier,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": elapsed_ms,
            })
            if on_attempt:
                try:
                    await on_attempt({
                        "phase": "failed",
                        "provider": name,
                        "tier": tier,
                        "error": f"{type(exc).__name__}: {exc}",
                        "latency_ms": elapsed_ms,
                    })
                except Exception:
                    pass
            if tier == 1:
                tier1_failed = True
            continue

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not isinstance(result, dict):
            result = {"result": result}
        merged = dict(result)
        merged["_provider_used"] = name
        merged["_tier_used"] = tier
        merged["_latency_ms"] = elapsed_ms

        if tier1_failed and tier > 1:
            logger.warning(
                "step %s recovered on tier %d via %s (after tier-1 failure)",
                step_label, tier, name,
            )
        else:
            logger.info(
                "step %s served by %s (tier %d) in %d ms",
                step_label, name, tier, elapsed_ms,
            )
        return merged

    raise AllProvidersFailedError(step_label, errors=error_log)


__all__ = [
    "try_chain",
    "AllProvidersFailedError",
    "build_speaking_chain",
    "should_run_sync_lipsync_refine",
    "evaluate_speaking_lipsync_refine",
    "should_run_motion_lipsync_refine",
    "RefineDecision",
]
