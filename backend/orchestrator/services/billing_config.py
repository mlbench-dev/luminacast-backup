"""Subscription / credits / metering pricing configuration.

Single source of truth for every dollar figure and multiplier used by the
billing system (`services/billing_service.py`, `routers/billing.py`,
`routers/webhooks.py`). Follows the same "plain config module, not env
vars" convention as `services/cost_rates.py` — these are business/pricing
decisions that should be a code review + deploy, not a runtime toggle.

All money is stored/compared in integer cents to avoid float drift in
billing math. `stripe_price_id_env` names the Settings field holding the
Stripe Price ID for that catalog entry (see config.py) — Price IDs are
deploy-environment-specific (test vs live mode) so they live in env vars,
while the dollar amounts they're expected to match live here for display
and for validating webhook payloads.
"""
from __future__ import annotations

import enum


class PlanTier(str, enum.Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    STUDIO = "studio"


class BillingInterval(str, enum.Enum):
    MONTH = "month"
    YEAR = "year"


class ProductionLevel(str, enum.Enum):
    STANDARD = "standard"
    PREMIUM = "premium"


# Cast.production_level also allows a legacy "quick" value pre-dating this
# pricing spec — bill it at the Standard rate rather than adding a third
# public tier.
PRODUCTION_LEVEL_ALIASES = {
    "quick": ProductionLevel.STANDARD.value,
    "simple": ProductionLevel.STANDARD.value,
    "standard": ProductionLevel.STANDARD.value,
    "premium": ProductionLevel.PREMIUM.value,
    "hd": ProductionLevel.PREMIUM.value,
    "hd_plus": ProductionLevel.PREMIUM.value,
}


def normalize_production_level(value: str | None) -> str:
    return PRODUCTION_LEVEL_ALIASES.get((value or "").lower(), ProductionLevel.STANDARD.value)


# Render usage = video duration (minutes) × production-level multiplier.
# e.g. a 2-minute video at the Premium multiplier bills 2 × 1.5 = 3 minutes.
PRODUCTION_LEVEL_MULTIPLIERS: dict[str, float] = {
    ProductionLevel.STANDARD.value: 1.0,
    ProductionLevel.PREMIUM.value: 1.5,
}

# Cast.quality (simple/hd/hd_plus) is a SEPARATE, independent multiplier on
# billable render-minutes, applied alongside (multiplied with) the
# production-level multiplier above — quality previously had zero effect on
# real billing despite being a real render-resolution/cost driver. The Setup
# tab surfaces these same multipliers on the Quality slider (×1.0 / ×1.4 /
# ×2.0 render minutes) so the displayed estimate and the real charge agree.
#
# Deliberately NOT normalized via normalize_production_level/
# PRODUCTION_LEVEL_ALIASES above — that dict already contains "hd"/"hd_plus"
# as keys (mapped to "premium", an unrelated pre-existing accident), so
# routing a quality value through it would silently misinterpret it as a
# production tier instead of a quality multiplier.
QUALITY_MULTIPLIERS: dict[str, float] = {
    "simple": 1.0,
    "hd": 1.4,
    "hd_plus": 2.0,
}


def normalize_quality(value: str | None) -> str:
    level = (value or "").lower()
    return level if level in QUALITY_MULTIPLIERS else "simple"

# Starter is marketed as "Unlimited social accounts" with a fair-use limit
# enforced (not advertised) internally. Configurable here rather than
# scattered through the social-account-connect flow.
STARTER_FAIR_USE_SOCIAL_ACCOUNTS = 10

PLAN_CATALOG: dict[str, dict] = {
    PlanTier.STARTER.value: {
        "name": "Starter",
        "monthly_price_cents": 3900,
        "annual_price_cents": 37200,  # $31/mo x 12, billed upfront
        "annual_monthly_equivalent_cents": 3100,
        "avatar_slots": 3,
        "render_minutes_per_month": 10,
        "live_stream_hours_per_month": 3,
        "social_accounts_limit": STARTER_FAIR_USE_SOCIAL_ACCOUNTS,  # fair-use only, displayed as Unlimited
        "production_level": ProductionLevel.STANDARD.value,
        "team_seats": 0,
        "stripe_price_id_env": {
            BillingInterval.MONTH.value: "STRIPE_PRICE_STARTER_MONTHLY",
            BillingInterval.YEAR.value: "STRIPE_PRICE_STARTER_ANNUAL",
        },
    },
    PlanTier.PRO.value: {
        "name": "Pro",
        "monthly_price_cents": 9900,
        "annual_price_cents": 94800,  # $79/mo x 12
        "annual_monthly_equivalent_cents": 7900,
        "avatar_slots": 7,
        "render_minutes_per_month": 25,
        "live_stream_hours_per_month": 15,
        "social_accounts_limit": None,  # unlimited, no internal cap
        "production_level": ProductionLevel.PREMIUM.value,
        "team_seats": 2,
        "stripe_price_id_env": {
            BillingInterval.MONTH.value: "STRIPE_PRICE_PRO_MONTHLY",
            BillingInterval.YEAR.value: "STRIPE_PRICE_PRO_ANNUAL",
        },
    },
    PlanTier.STUDIO.value: {
        "name": "Studio",
        "monthly_price_cents": 24900,
        "annual_price_cents": 238800,  # $199/mo x 12
        "annual_monthly_equivalent_cents": 19900,
        "avatar_slots": 20,
        "render_minutes_per_month": 70,
        "live_stream_hours_per_month": 40,
        "social_accounts_limit": None,
        "production_level": ProductionLevel.PREMIUM.value,
        "team_seats": 5,
        "stripe_price_id_env": {
            BillingInterval.MONTH.value: "STRIPE_PRICE_STUDIO_MONTHLY",
            BillingInterval.YEAR.value: "STRIPE_PRICE_STUDIO_ANNUAL",
        },
    },
}

# Roughly 20% off — actual per-plan annual prices above are the source of
# truth for billing; this is only for the UI's "~20% off" badge.
ANNUAL_DISCOUNT_LABEL = "20% off"

FREE_TIER = {
    "name": "Free",
    "avatar_slots": 1,
    "render_minutes": 2,  # one-time allowance, not monthly — see billing_service
    "watermarked": True,
}

# Overage — charged once a subscriber exceeds their plan's included monthly
# allowance. Render rate depends on which production level was used for
# that render, matching the plan's own multiplier structure.
OVERAGE_RATE_CENTS = {
    "render_per_minute": {
        ProductionLevel.STANDARD.value: 500,   # $5.00/min
        ProductionLevel.PREMIUM.value: 700,    # $7.00/min
    },
    "live_stream_per_hour": 300,      # $3.00/hr
    "avatar_slot_per_month": 900,     # $9.00/mo per extra slot
}

# Non-subscriber PAYG (no active subscription at all) — flat rate regardless
# of production level, intended to nudge the trial → subscribe funnel.
NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE = 800  # $8.00/min

# Prepaid PAYG credit packs (id -> face-value cents). Stripe Price IDs are
# looked up the same way as subscription plans, via env var name below.
CREDIT_PACKS: dict[str, dict] = {
    "credits_20": {"amount_cents": 2000, "stripe_price_id_env": "STRIPE_PRICE_CREDITS_20"},
    "credits_50": {"amount_cents": 5000, "stripe_price_id_env": "STRIPE_PRICE_CREDITS_50"},
    "credits_100": {"amount_cents": 10000, "stripe_price_id_env": "STRIPE_PRICE_CREDITS_100"},
}

CREDIT_EXPIRY_MONTHS = 12


def get_plan(plan_id: str) -> dict:
    plan = PLAN_CATALOG.get(plan_id)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_id}")
    return plan


def get_overage_render_rate_cents(production_level: str) -> int:
    level = normalize_production_level(production_level)
    return OVERAGE_RATE_CENTS["render_per_minute"][level]
