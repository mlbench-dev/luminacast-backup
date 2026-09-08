"""Usage tracker — provider cost calculators and the `log_usage` writer.

PR α (this PR) lays the foundation. PR β instruments every billable site
to call `log_usage()` after a successful provider call. PR γ adds the
admin dashboard, the user billing page, and the daily rollup.

The cost calculators are pure functions that take provider metrics and
return USD. `log_usage()` is the single writer — every billable site in
the codebase will call this and only this.
"""

import logging
import uuid
from typing import Optional

from models.usage import UsageEvent
from services.cost_rates import COST_RATES, MARKUP_MULTIPLIER

logger = logging.getLogger(__name__)


# ── LLM cost ─────────────────────────────────────────────────────────────

# Map of common model-name variants → the canonical key in COST_RATES.
# Extend this when a new OpenRouter model is wired up. Match keys must be
# lowercase substrings; lookup is "first match wins" so order from most
# specific (e.g. "haiku") to least specific.
_LLM_ALIAS_MAP = {
    # Opus 4.8 — creative-generation model (Step 9). Pricier than Sonnet.
    "claude-opus-4.8":     "openrouter/claude-opus-4.8",
    "claude-opus-4-8":     "openrouter/claude-opus-4.8",
    "opus-4.8":            "openrouter/claude-opus-4.8",
    # Sonnet 4.x family — same per-token pricing across point releases
    "claude-sonnet-4.6":   "openrouter/claude-sonnet-4.6",
    "claude-sonnet-4-6":   "openrouter/claude-sonnet-4.6",
    "sonnet-4.6":          "openrouter/claude-sonnet-4.6",
    "claude-sonnet-4.5":   "openrouter/claude-sonnet-4.5",
    "claude-sonnet-4-5":   "openrouter/claude-sonnet-4.5",
    "sonnet-4.5":          "openrouter/claude-sonnet-4.5",
    "claude-sonnet-4":     "openrouter/claude-sonnet-4",
    "sonnet-4":            "openrouter/claude-sonnet-4",
    # Haiku
    "claude-3-haiku":      "openrouter/claude-3-haiku",
    "haiku":               "openrouter/claude-3-haiku",
}


def calculate_llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Provider cost in USD for an OpenRouter LLM call.

    Resolution order:
      1. exact match against COST_RATES (after normalising the model name
         to e.g. "openrouter/claude-sonnet-4.6")
      2. alias map (handles "claude-sonnet-4-6", "sonnet-4.6", etc.)
      3. fallback to Sonnet pricing + a warning log so unmapped models
         show up in production

    Returns USD rounded to 6 decimal places.
    """
    if not model:
        rates = COST_RATES["openrouter/claude-sonnet-4.6"]
        return _llm_cost_from_rates(rates, input_tokens, output_tokens)

    normalised = model.lower().strip()
    # Drop a leading vendor prefix like "anthropic/" so "anthropic/claude-sonnet-4.6"
    # collapses to "claude-sonnet-4.6"
    bare = normalised.split("/", 1)[-1] if "/" in normalised else normalised

    # 1. exact key match
    exact_key = f"openrouter/{bare}"
    rates = COST_RATES.get(exact_key)
    if isinstance(rates, dict):
        return _llm_cost_from_rates(rates, input_tokens, output_tokens)

    # 2. alias map (longest alias first to avoid "haiku" eating "sonnet-haiku-X")
    for alias in sorted(_LLM_ALIAS_MAP, key=len, reverse=True):
        if alias in bare:
            mapped = _LLM_ALIAS_MAP[alias]
            rates = COST_RATES.get(mapped)
            if isinstance(rates, dict):
                return _llm_cost_from_rates(rates, input_tokens, output_tokens)

    # 3. fallback — log so unmapped models surface in prod
    logger.warning(
        "calculate_llm_cost: unmapped LLM model %r (input=%d output=%d); "
        "falling back to Sonnet pricing. Add an entry to COST_RATES or "
        "_LLM_ALIAS_MAP if this model will be used regularly.",
        model, input_tokens, output_tokens,
    )
    rates = COST_RATES["openrouter/claude-sonnet-4.6"]
    return _llm_cost_from_rates(rates, input_tokens, output_tokens)


def _llm_cost_from_rates(rates: dict, input_tokens: int, output_tokens: int) -> float:
    input_cost = (input_tokens / 1_000_000) * rates["input_per_1m"]
    output_cost = (output_tokens / 1_000_000) * rates["output_per_1m"]
    return round(input_cost + output_cost, 6)


# ── GPU render cost ──────────────────────────────────────────────────────


def calculate_gpu_render_cost(provider: str, duration_seconds: float) -> float:
    """Provider cost in USD for a GPU render block.

    HOSTKEY is the dedicated server (free at the per-render level — fixed
    monthly cost is tracked separately on the admin dashboard). RunPod and
    Modal are per-second serverless.
    """
    if duration_seconds is None or duration_seconds <= 0:
        return 0.0
    if provider == "hostkey":
        return 0.0
    if provider == "runpod":
        return round(COST_RATES["runpod/a40_serverless"] * duration_seconds, 6)
    if provider == "modal":
        return round(COST_RATES["modal/l40s"] * duration_seconds, 6)
    return 0.0


# ── fal.ai video cost ────────────────────────────────────────────────────

# Same first-match-wins idea as the LLM alias map. Order from most
# specific to least.
_FAL_VIDEO_ALIASES = (
    ("wan-2.5",     "fal/wan_2.5_t2v"),
    ("wan_2.5",     "fal/wan_2.5_t2v"),
    ("wan-2.2",     "fal/wan_2.2_t2v"),
    ("wan_2.2",     "fal/wan_2.2_t2v"),
    ("kling-2.5",   "fal/kling_2.5_turbo_pro"),
    ("kling_2.5",   "fal/kling_2.5_turbo_pro"),
    ("veo-3",       "fal/veo_3"),
    ("veo_3",       "fal/veo_3"),
)


def calculate_fal_video_cost(model: str, video_seconds: float) -> float:
    """Provider cost in USD for a fal.ai T2V/I2V job.

    `video_seconds` is the **output** video length, not GPU runtime.
    """
    if video_seconds is None or video_seconds <= 0:
        return 0.0

    per_second = COST_RATES["fal/wan_2.2_t2v"]  # default — older Wan rate
    if model:
        bare = model.lower()
        for alias, key in _FAL_VIDEO_ALIASES:
            if alias in bare:
                per_second = COST_RATES[key]
                break
    return round(per_second * video_seconds, 4)


# Map dispatcher backend tag → per-output-second COST_RATES key for the
# non-T2V/I2V video-second backends. fal_t2v / fal_i2v are handled separately
# by delegating to calculate_fal_video_cost (model-aware rate selection).
_VIDEO_SECOND_BACKEND_RATE_KEYS = {
    "fal_hallo":               "fal/fal_hallo",
    "fal_sync_lipsync_v2_pro": "fal/sync_lipsync_v2_pro",
    "fal_sync_lipsync_v3":     "fal/sync_lipsync_v3",
    "product_elements_bake":   "fal/kling_elements_v3_pro",
    "wavespeed":               "wavespeed/infinitalk",
}


def calculate_video_second_cost(
    backend: str, model: Optional[str], video_seconds: float
) -> float:
    """Provider cost in USD for one video-second backend bake.

    `video_seconds` is the **output** clip length (slot duration), not GPU
    wall-clock runtime. PR-I: before this helper existed these backends were
    routed through `calculate_gpu_render_cost(provider="fal_ai"/"wavespeed")`,
    which returned $0.00 because those providers are not in the GPU rate table.

    Dispatch:
      * fal_t2v / fal_i2v → `calculate_fal_video_cost` (model-aware rate).
      * fal_hallo / fal_sync_lipsync_v2_pro / fal_sync_lipsync_v3 /
        product_elements_bake / wavespeed → fixed per-second `COST_RATES` entry.
      * anything else → 0.0 with a warn log + Sentry breadcrumb so the
        unmapped backend surfaces in production instead of silently billing $0.
    """
    if video_seconds is None or video_seconds <= 0:
        return 0.0

    if backend in ("fal_t2v", "fal_i2v"):
        return calculate_fal_video_cost(model, video_seconds)

    rate_key = _VIDEO_SECOND_BACKEND_RATE_KEYS.get(backend)
    if rate_key is not None:
        return round(COST_RATES[rate_key] * video_seconds, 4)

    logger.warning(
        "calculate_video_second_cost: unmapped video-second backend %r "
        "(model=%r seconds=%.3f); billing $0.00. Add a rate to COST_RATES and "
        "an entry to _VIDEO_SECOND_BACKEND_RATE_KEYS.",
        backend, model, float(video_seconds),
    )
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category="usage",
            level="warning",
            message=f"calculate_video_second_cost unmapped backend: {backend}",
            data={"backend": backend, "model": model, "video_seconds": video_seconds},
        )
    except Exception as e:  # pragma: no cover — breadcrumb is best-effort
        import sentry_sdk

        sentry_sdk.capture_exception(e)
    return 0.0


# ── fal.ai image cost ────────────────────────────────────────────────────


def calculate_fal_image_cost(model: str, count: int = 1) -> float:
    """Provider cost in USD for a fal.ai image generation job."""
    bare = (model or "").lower()
    if "kontext-max" in bare or "kontext/max" in bare or "kontext_max" in bare:
        per_image = COST_RATES["fal/flux_kontext_max"]
    elif "kontext" in bare:
        per_image = COST_RATES["fal/flux_kontext_pro"]
    elif "nano" in bare or "banana" in bare:
        per_image = (
            COST_RATES["fal/nano_banana_pro_4k"] if "4k" in bare
            else COST_RATES["fal/nano_banana_pro"]
        )
    elif "qwen" in bare:
        per_image = COST_RATES["fal/qwen_angles"]
    else:
        per_image = COST_RATES["fal/flux_kontext_pro"]
    return round(per_image * count, 4)


# ── log_usage ────────────────────────────────────────────────────────────


async def log_usage(
    db,
    *,
    user_id: str,
    event_type: str,
    provider: str,
    provider_cost_usd: float,
    quantity: float = 1.0,
    quantity_unit: str = "call",
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    render_id: Optional[str] = None,
    block_id: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    provider_job_id: Optional[str] = None,
    provider_model: Optional[str] = None,
) -> UsageEvent:
    """Insert one UsageEvent. **Does not commit** — the caller's
    transaction owns the lifecycle.

    Two valid call patterns:

    1. **Request-scoped** — the FastAPI route already has an open
       AsyncSession from `Depends(get_db)`. Call `log_usage(db, ...)` and
       let the route commit at the end of its work::

           await log_usage(db, user_id=user.id, event_type="script_generation", ...)
           await db.commit()

    2. **Background task / Celery** — open a fresh session, call
       `log_usage`, then commit and close::

           async with async_session_factory() as session:
               await log_usage(session, user_id=..., event_type=..., ...)
               await session.commit()

    The user-facing price is computed here from `provider_cost_usd *
    MARKUP_MULTIPLIER` so every billable action prices the same way.
    """

    user_price = round(provider_cost_usd * MARKUP_MULTIPLIER, 4)

    event = UsageEvent(
        id=f"usg_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        event_type=event_type,
        provider=provider,
        provider_cost_usd=round(provider_cost_usd, 6),
        user_price_usd=user_price,
        quantity=quantity,
        quantity_unit=quantity_unit,
        resource_type=resource_type,
        resource_id=resource_id,
        render_id=render_id,
        block_id=block_id,
        duration_seconds=duration_seconds,
        provider_job_id=provider_job_id,
        provider_model=provider_model,
    )

    db.add(event)
    # No commit on purpose — see docstring.

    logger.info(
        "USAGE: user=%s type=%s provider=%s cost=$%.6f price=$%.4f qty=%.2f %s",
        (user_id or "")[:12], event_type, provider, provider_cost_usd, user_price,
        quantity, quantity_unit,
    )

    return event
