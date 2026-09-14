"""Centralized subscription + credits + usage-metering service.

Every usage deduction in the product (render minutes, live-stream hours,
avatar slots, PAYG credits) goes through this module — nothing elsewhere
should mutate a `UsagePeriod`, `CreditWallet`, or `Subscription` row
directly. That's what makes this "centralized metering": one place decides
whether a unit of usage comes from included allowance, PAYG credits, or
overage billing, and one place writes the audit row explaining why.

Money is cents (int) everywhere. Usage quantities are floats (fractional
minutes/hours are real — a 90-second render is 1.5 billable minutes).

Idempotency: `deduct_render_usage` / `deduct_livestream_usage` are keyed by
a unique DB constraint on `render_id` / `stream_session_id` — calling them
twice for the same render/stream is a safe no-op (returns the existing
record instead of double-billing). This is what protects against a retried
Celery task re-billing a render after a worker crash (see the terminal-
state guard already in tasks/cast_render.py).
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import sentry_sdk
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.avatar import Avatar
from models.billing import (
    AvatarSlotPurchase,
    CreditTransaction,
    CreditTransactionType,
    CreditWallet,
    LiveStreamUsageRecord,
    OverageBillingMethod,
    OverageCharge,
    OverageChargeResourceType,
    RenderUsageRecord,
    Subscription,
    SubscriptionEvent,
    SubscriptionEventType,
    SubscriptionPayment,
    SubscriptionStatus,
    UsagePeriod,
)
from services.billing_config import (
    CREDIT_EXPIRY_MONTHS,
    CREDIT_PACKS,
    FREE_TIER,
    NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE,
    OVERAGE_RATE_CENTS,
    PLAN_CATALOG,
    PRODUCTION_LEVEL_MULTIPLIERS,
    QUALITY_MULTIPLIERS,
    get_overage_render_rate_cents,
    normalize_production_level,
    normalize_quality,
)

logger = logging.getLogger(__name__)

_PLAN_RANK = {"starter": 0, "pro": 1, "studio": 2}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _naive_utc_now() -> datetime:
    # Every DateTime column in this codebase is stored naive-UTC (see
    # tasks/usage_rollup.py's comment on the same convention) — match it so
    # comparisons against DB values don't raise "can't compare offset-naive
    # and offset-aware datetimes".
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp day for month-end overflow (e.g. Jan 31 + 1 month -> Feb 28).
    day = dt.day
    while True:
        try:
            return dt.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1


# ── Subscription / plan lookup ──────────────────────────────────────────


async def get_active_subscription(db: AsyncSession, owner_id: str) -> Optional[Subscription]:
    return (
        await db.execute(select(Subscription).where(Subscription.user_id == owner_id))
    ).scalar_one_or_none()


def _is_billable(subscription: Optional[Subscription]) -> bool:
    return subscription is not None and subscription.status in (
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.PAST_DUE.value,
    )


async def _is_admin(db: AsyncSession, owner_id: str) -> bool:
    """Admin accounts never need to buy a subscription or PAYG credits —
    used by check_render_preflight and check_avatar_slot_available (the
    only two functions that actually BLOCK an action with a "subscribe to
    continue" error) to bypass those gates entirely for admins. Local
    import avoids a circular import with models.user; defaults to
    non-admin on any lookup failure (fail closed, never silently grants
    the bypass)."""
    from models.user import User, UserRole
    user = await db.get(User, owner_id)
    return bool(user and user.role == UserRole.ADMIN)


def get_plan_config(plan_id: str) -> dict:
    return PLAN_CATALOG[plan_id]


def resolve_plan_from_price_id(price_id: str) -> Optional[tuple[str, str]]:
    """Reverse-lookup (plan, interval) from a Stripe Price ID, by scanning
    the configured env var for every plan/interval combo. Used by the
    webhook handler so subscription state always reflects what Stripe's
    price actually is, not just whatever metadata was set at checkout time
    (which could go stale if a plan change happens via the Stripe-hosted
    portal instead of our own checkout flow)."""
    from config import settings

    for plan_id, plan in PLAN_CATALOG.items():
        for interval, env_name in plan["stripe_price_id_env"].items():
            if getattr(settings, env_name, "") == price_id:
                return plan_id, interval
    return None


def stripe_timestamp_to_naive_utc(ts: Optional[int]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


# ── Usage periods ────────────────────────────────────────────────────────


async def get_or_create_current_usage_period(
    db: AsyncSession, owner_id: str, *, now: Optional[datetime] = None
) -> UsagePeriod:
    """Return the `UsagePeriod` covering `now` for this owner, creating the
    next monthly slice if needed.

    For an active subscription this releases allowance one month at a time
    even when the Stripe billing cycle is annual (spec: annual subscribers
    do NOT get the full year's allowance up front) — see `_add_months`
    walking `subscription.current_period_start` forward. Allowance never
    rolls over: a new period always starts with `*_used = 0`, regardless
    of how much of the previous period went unused.

    Owners with no billable subscription get a single, non-expiring
    `is_free_tier=True` period (spec: the free allowance isn't monthly, and
    isn't replaced by a new one once created).
    """
    now = now or _naive_utc_now()
    subscription = await get_active_subscription(db, owner_id)

    if not _is_billable(subscription):
        free_period = (
            await db.execute(
                select(UsagePeriod).where(
                    UsagePeriod.user_id == owner_id, UsagePeriod.is_free_tier.is_(True)
                )
            )
        ).scalar_one_or_none()
        if free_period:
            return free_period
        free_period = UsagePeriod(
            id=_id("usp"),
            user_id=owner_id,
            subscription_id=None,
            period_start=now,
            period_end=None,
            is_free_tier=True,
            plan="free",
            render_minutes_included=float(FREE_TIER["render_minutes"]),
            render_minutes_used=0.0,
            live_stream_hours_included=0.0,
            live_stream_hours_used=0.0,
        )
        db.add(free_period)
        await db.commit()
        return free_period

    latest = (
        await db.execute(
            select(UsagePeriod)
            .where(UsagePeriod.subscription_id == subscription.id)
            .order_by(UsagePeriod.period_start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if latest and latest.period_end and latest.period_start <= now < latest.period_end:
        return latest

    period_start = subscription.current_period_start
    if latest and latest.period_end:
        period_start = latest.period_end
    # Walk forward month-by-month from the subscription anchor until we
    # cover `now` — handles the (rare) case of a backfill/late webhook
    # rather than assuming exactly one period has elapsed.
    while period_start <= now:
        candidate_end = min(_add_months(period_start, 1), subscription.current_period_end)
        if period_start <= now < candidate_end or candidate_end >= subscription.current_period_end:
            period_end = candidate_end
            break
        period_start = candidate_end
    else:
        period_end = min(_add_months(period_start, 1), subscription.current_period_end)

    plan = get_plan_config(subscription.plan)
    new_period = UsagePeriod(
        id=_id("usp"),
        user_id=owner_id,
        subscription_id=subscription.id,
        period_start=period_start,
        period_end=period_end,
        is_free_tier=False,
        plan=subscription.plan,
        render_minutes_included=float(plan["render_minutes_per_month"]),
        render_minutes_used=0.0,
        live_stream_hours_included=float(plan["live_stream_hours_per_month"]),
        live_stream_hours_used=0.0,
    )
    db.add(new_period)
    await db.commit()
    return new_period


async def refresh_usage_periods(db: AsyncSession) -> dict:
    """Ensure every active/past-due subscription has a UsagePeriod covering
    today, and flip subscriptions whose Stripe period has lapsed without a
    renewal to `expired`. Intended to run daily via Celery beat
    (`tasks.billing_tasks.refresh_usage_periods_task`) as a local safety
    net — Stripe webhooks are the primary source of truth for renewals,
    this just catches the case where a renewal never arrived (e.g. Stripe
    is misconfigured, or the account truly lapsed).
    """
    now = _naive_utc_now()
    subs = (
        await db.execute(
            select(Subscription).where(
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.PAST_DUE.value]
                )
            )
        )
    ).scalars().all()

    periods_created = 0
    expired = 0
    for sub in subs:
        if sub.current_period_end < now:
            sub.status = SubscriptionStatus.EXPIRED.value
            db.add(
                SubscriptionEvent(
                    id=_id("sev"),
                    subscription_id=sub.id,
                    user_id=sub.user_id,
                    event_type=SubscriptionEventType.EXPIRED.value,
                    from_plan=sub.plan,
                    note="Billing period ended with no renewal recorded",
                )
            )
            expired += 1
            continue
        before = (
            await db.execute(
                select(UsagePeriod.id).where(
                    UsagePeriod.subscription_id == sub.id,
                    UsagePeriod.period_start <= now,
                    UsagePeriod.period_end > now,
                )
            )
        ).scalar_one_or_none()
        if not before:
            await get_or_create_current_usage_period(db, sub.user_id, now=now)
            periods_created += 1
    await db.commit()
    return {"periods_created": periods_created, "subscriptions_expired": expired}


# ── Render usage metering ───────────────────────────────────────────────


class BillableMinutesResult(NamedTuple):
    """Named fields instead of a bare tuple — this grew from 3 values to 6
    when quality became a second, independent multiplier alongside
    production_level; positional unpacking of that many billing values is
    exactly the kind of thing that produces a silent field-order bug."""
    billable_minutes: float
    production_level: str
    production_multiplier: float
    quality: str
    quality_multiplier: float
    combined_multiplier: float


def compute_billable_minutes(
    duration_seconds: float, production_level: str, quality: str = "simple",
) -> BillableMinutesResult:
    """render usage = video duration (minutes) x production-level multiplier
    x quality multiplier. Quality (simple/hd/hd_plus) is a second,
    independent cost driver on top of production_level — same mechanical
    role, applied multiplicatively — see QUALITY_MULTIPLIERS."""
    level = normalize_production_level(production_level)
    production_multiplier = PRODUCTION_LEVEL_MULTIPLIERS[level]
    quality_level = normalize_quality(quality)
    quality_multiplier = QUALITY_MULTIPLIERS[quality_level]
    combined_multiplier = production_multiplier * quality_multiplier
    duration_minutes = max(float(duration_seconds), 0.0) / 60.0
    billable_minutes = round(duration_minutes * combined_multiplier, 4)
    return BillableMinutesResult(
        billable_minutes=billable_minutes,
        production_level=level,
        production_multiplier=production_multiplier,
        quality=quality_level,
        quality_multiplier=quality_multiplier,
        combined_multiplier=combined_multiplier,
    )


async def check_render_preflight(db: AsyncSession, owner_id: str) -> None:
    """Pre-flight gate before *starting* a render.

    Deliberately does NOT check whether enough allowance remains for the
    render about to happen (its final duration isn't known yet — spec edge
    case: a user with 2 minutes remaining can still consume 5 billable
    minutes and go into overage). It only blocks the case where there is
    genuinely no possible way to bill the render at all: free tier
    exhausted, no active subscription, and zero PAYG credits.
    """
    if await _is_admin(db, owner_id):
        return
    subscription = await get_active_subscription(db, owner_id)
    if _is_billable(subscription):
        return
    wallet = await get_or_create_credit_wallet(db, owner_id)
    if wallet.balance_cents > 0:
        return
    period = await get_or_create_current_usage_period(db, owner_id)
    if period.is_free_tier and period.render_minutes_used < period.render_minutes_included:
        return
    raise HTTPException(
        status_code=402,
        detail=(
            "Your free render minutes are used up. Subscribe to a plan or "
            "buy PAYG credits to keep rendering."
        ),
    )


async def deduct_render_usage(
    db: AsyncSession,
    *,
    user_id: str,
    owner_id: str,
    render_id: str,
    cast_id: Optional[str],
    duration_seconds: float,
    production_level: str,
    quality: str = "simple",
) -> RenderUsageRecord:
    """Bill a completed render. Cascades: included allowance -> PAYG
    credits -> overage. Idempotent on `render_id`."""
    existing = (
        await db.execute(select(RenderUsageRecord).where(RenderUsageRecord.render_id == render_id))
    ).scalar_one_or_none()
    if existing:
        return existing

    result = compute_billable_minutes(duration_seconds, production_level, quality)
    billable_minutes = result.billable_minutes
    level = result.production_level

    # Bug (more serious than the start-of-render gate): check_render_preflight
    # was fixed to never BLOCK an admin from starting a render, but THIS
    # function — which runs AFTER the render finishes and is what actually
    # deducts wallet credits or fires a REAL off-session Stripe charge via
    # _attempt_overage_charge — recomputes billing completely independently
    # and had no admin awareness at all. An admin whose included/free
    # minutes were exhausted could start a render fine (gate bypassed) and
    # then have their card genuinely charged once it completed. Still
    # record the render for accurate usage analytics, but treat the whole
    # thing as free — never touch the wallet or Stripe for an admin.
    if await _is_admin(db, owner_id):
        period = await get_or_create_current_usage_period(db, owner_id)
        record = RenderUsageRecord(
            id=_id("rur"),
            user_id=user_id,
            render_id=render_id,
            cast_id=cast_id,
            usage_period_id=period.id,
            duration_seconds=float(duration_seconds or 0.0),
            production_level=level,
            multiplier=result.production_multiplier,
            quality=result.quality,
            quality_multiplier=result.quality_multiplier,
            billable_minutes=billable_minutes,
            included_minutes_applied=0.0,
            credits_minutes_applied=0.0,
            credits_amount_cents=0,
            overage_minutes_applied=0.0,
            overage_rate_cents_per_minute=None,
            overage_amount_cents=0,
            free_minutes_applied=billable_minutes,
        )
        db.add(record)
        try:
            await db.commit()
        except IntegrityError:
            # Lost a race with a concurrent redelivery of the same render's
            # completion event — see the identical handling below.
            await db.rollback()
            existing = (
                await db.execute(select(RenderUsageRecord).where(RenderUsageRecord.render_id == render_id))
            ).scalar_one_or_none()
            if existing:
                return existing
            raise
        return record

    remaining = billable_minutes

    subscription = await get_active_subscription(db, owner_id)
    period = await get_or_create_current_usage_period(db, owner_id)

    free_applied = 0.0
    included_applied = 0.0
    if period.is_free_tier:
        available = max(period.render_minutes_included - period.render_minutes_used, 0.0)
        free_applied = min(remaining, available)
        period.render_minutes_used += free_applied
        remaining = round(remaining - free_applied, 4)
    else:
        available = max(period.render_minutes_included - period.render_minutes_used, 0.0)
        included_applied = min(remaining, available)
        period.render_minutes_used += included_applied
        remaining = round(remaining - included_applied, 4)

    rate_cents = (
        get_overage_render_rate_cents(level)
        if _is_billable(subscription)
        else NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE
    )

    credits_applied = 0.0
    credits_amount_cents = 0
    if remaining > 0:
        wallet = await get_or_create_credit_wallet(db, owner_id)
        max_minutes_from_wallet = wallet.balance_cents / rate_cents if rate_cents else 0
        credits_applied = round(min(remaining, max_minutes_from_wallet), 4)
        if credits_applied > 0:
            credits_amount_cents = math.ceil(credits_applied * rate_cents)
            credits_amount_cents = min(credits_amount_cents, wallet.balance_cents)
            await _deduct_credits(
                db,
                wallet,
                credits_amount_cents,
                description=f"Render {render_id} ({level}, {credits_applied:.2f} min)",
                related_render_id=render_id,
            )
            remaining = round(remaining - credits_applied, 4)

    overage_applied = 0.0
    overage_amount_cents = 0
    overage_rate = None
    if remaining > 0:
        overage_applied = remaining
        overage_rate = rate_cents
        overage_amount_cents = math.ceil(overage_applied * rate_cents)
        billing_method = await _attempt_overage_charge(
            db,
            owner_id=owner_id,
            subscription=subscription,
            amount_cents=overage_amount_cents,
        )
        db.add(
            OverageCharge(
                id=_id("ovc"),
                user_id=owner_id,
                usage_period_id=period.id,
                resource_type=OverageChargeResourceType.RENDER.value,
                quantity=overage_applied,
                rate_cents=overage_rate,
                amount_cents=overage_amount_cents,
                billing_method=billing_method,
                related_render_id=render_id,
            )
        )

    record = RenderUsageRecord(
        id=_id("rur"),
        user_id=user_id,
        render_id=render_id,
        cast_id=cast_id,
        usage_period_id=period.id,
        duration_seconds=float(duration_seconds or 0.0),
        production_level=level,
        multiplier=result.production_multiplier,
        quality=result.quality,
        quality_multiplier=result.quality_multiplier,
        billable_minutes=billable_minutes,
        included_minutes_applied=included_applied,
        credits_minutes_applied=credits_applied,
        credits_amount_cents=credits_amount_cents,
        overage_minutes_applied=overage_applied,
        overage_rate_cents_per_minute=overage_rate,
        overage_amount_cents=overage_amount_cents,
        free_minutes_applied=free_applied,
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race with a concurrent redelivery of the same render's
        # completion event — the other writer already inserted the
        # unique(render_id) row. Roll back our half-applied allowance
        # mutations and return the winner's record instead of double-billing.
        await db.rollback()
        existing = (
            await db.execute(select(RenderUsageRecord).where(RenderUsageRecord.render_id == render_id))
        ).scalar_one_or_none()
        if existing:
            return existing
        raise
    return record


# ── Live-stream usage metering ──────────────────────────────────────────


async def deduct_livestream_usage(
    db: AsyncSession,
    *,
    user_id: str,
    owner_id: str,
    stream_session_id: str,
    duration_minutes: float,
) -> LiveStreamUsageRecord:
    existing = (
        await db.execute(
            select(LiveStreamUsageRecord).where(
                LiveStreamUsageRecord.stream_session_id == stream_session_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    billable_hours = round(max(float(duration_minutes), 0.0) / 60.0, 4)
    remaining = billable_hours

    subscription = await get_active_subscription(db, owner_id)
    period = await get_or_create_current_usage_period(db, owner_id)

    included_applied = 0.0
    if not period.is_free_tier:
        available = max(period.live_stream_hours_included - period.live_stream_hours_used, 0.0)
        included_applied = min(remaining, available)
        period.live_stream_hours_used += included_applied
        remaining = round(remaining - included_applied, 4)

    rate_cents = OVERAGE_RATE_CENTS["live_stream_per_hour"]

    credits_applied = 0.0
    credits_amount_cents = 0
    if remaining > 0:
        wallet = await get_or_create_credit_wallet(db, owner_id)
        max_hours_from_wallet = wallet.balance_cents / rate_cents if rate_cents else 0
        credits_applied = round(min(remaining, max_hours_from_wallet), 4)
        if credits_applied > 0:
            credits_amount_cents = math.ceil(credits_applied * rate_cents)
            credits_amount_cents = min(credits_amount_cents, wallet.balance_cents)
            await _deduct_credits(
                db,
                wallet,
                credits_amount_cents,
                description=f"Live stream {stream_session_id} ({credits_applied:.2f} hr)",
                related_stream_session_id=stream_session_id,
            )
            remaining = round(remaining - credits_applied, 4)

    overage_applied = 0.0
    overage_amount_cents = 0
    overage_rate = None
    if remaining > 0:
        overage_applied = remaining
        overage_rate = rate_cents
        overage_amount_cents = math.ceil(overage_applied * rate_cents)
        billing_method = await _attempt_overage_charge(
            db, owner_id=owner_id, subscription=subscription, amount_cents=overage_amount_cents
        )
        db.add(
            OverageCharge(
                id=_id("ovc"),
                user_id=owner_id,
                usage_period_id=period.id,
                resource_type=OverageChargeResourceType.LIVE_STREAM.value,
                quantity=overage_applied,
                rate_cents=overage_rate,
                amount_cents=overage_amount_cents,
                billing_method=billing_method,
                related_stream_session_id=stream_session_id,
            )
        )

    record = LiveStreamUsageRecord(
        id=_id("lsu"),
        user_id=user_id,
        stream_session_id=stream_session_id,
        usage_period_id=period.id,
        duration_minutes=float(duration_minutes or 0.0),
        billable_hours=billable_hours,
        included_hours_applied=included_applied,
        credits_hours_applied=credits_applied,
        credits_amount_cents=credits_amount_cents,
        overage_hours_applied=overage_applied,
        overage_rate_cents_per_hour=overage_rate,
        overage_amount_cents=overage_amount_cents,
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(LiveStreamUsageRecord).where(
                    LiveStreamUsageRecord.stream_session_id == stream_session_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        raise
    return record


async def _attempt_overage_charge(
    db: AsyncSession, *, owner_id: str, subscription: Optional[Subscription], amount_cents: int
) -> str:
    """Best-effort immediate off-session charge for an overage amount.
    Never raises — billing state (pending/failed) is recorded either way,
    per the spec's "never silently exceed limits without recording how it
    will be billed" requirement. Falls back to `pending` (collected at next
    manual reconciliation / dunning) when there's no subscription/customer
    to charge or the charge attempt fails.
    """
    if amount_cents <= 0:
        return OverageBillingMethod.CREDITS.value
    if not subscription or not subscription.stripe_customer_id:
        return OverageBillingMethod.PENDING.value
    try:
        from services.stripe_billing import get_stripe_billing_service

        stripe_service = get_stripe_billing_service()

        # Same gap as the avatar-slot purchase endpoint (fixed alongside
        # this): without an explicit payment_method, Stripe falls back to
        # the CUSTOMER's default_payment_method — a different field from
        # the SUBSCRIPTION's own default_payment_method, and not reliably
        # set even for an actively-paying customer. Because this function
        # never raises (failures silently become "pending" — see docstring),
        # this was likely failing quietly for real customers with no
        # visible error anywhere, just an overage charge that never
        # actually collected. Resolve the subscription's own default
        # payment method — the one that already charges every renewal —
        # instead of relying on the customer-level default.
        payment_method_id = None
        if subscription.stripe_subscription_id:
            try:
                stripe_sub = await stripe_service.get_subscription(subscription.stripe_subscription_id)
                pm = stripe_sub.get("default_payment_method")
                payment_method_id = pm.get("id") if isinstance(pm, dict) else pm
            except Exception as pm_exc:
                sentry_sdk.capture_exception(pm_exc)

        await stripe_service.create_off_session_payment(
            amount_cents=amount_cents,
            customer_id=subscription.stripe_customer_id,
            payment_method_id=payment_method_id,
            metadata={"owner_id": owner_id, "kind": "overage"},
        )
        return OverageBillingMethod.STRIPE_CHARGE.value
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("Overage charge failed for owner %s: %s", owner_id, exc)
        return OverageBillingMethod.FAILED.value


# ── Avatar slots ─────────────────────────────────────────────────────────


async def get_avatar_slot_summary(db: AsyncSession, owner_id: str) -> dict:
    used = (
        await db.execute(
            select(Avatar).where(Avatar.user_id == owner_id, Avatar.deleted_at.is_(None))
        )
    ).scalars().all()
    used_count = len(used)

    # Bug: check_avatar_slot_available (the actual gate) was fixed to skip
    # entirely for admins, but this summary — what the avatar Setup page
    # fetches to decide whether to gray out "Create Avatar" — wasn't, so an
    # admin over the free-tier count still saw the create button disabled
    # client-side and never got a chance to reach the now-fixed backend at
    # all. `unlimited` is for a future frontend read; the padded
    # `included`/`remaining` numbers below make an admin's create button
    # correctly stay enabled TODAY even without any frontend change, since
    # remaining is derived from them the exact same way as everyone else's.
    if await _is_admin(db, owner_id):
        headroom = 1000
        return {
            "included": used_count + headroom,
            "purchased": 0,
            "total": used_count + headroom,
            "used": used_count,
            "remaining": headroom,
            "unlimited": True,
        }

    subscription = await get_active_subscription(db, owner_id)
    if _is_billable(subscription):
        plan = get_plan_config(subscription.plan)
        included = plan["avatar_slots"]
        purchased = subscription.extra_avatar_slots or 0
    else:
        included = FREE_TIER["avatar_slots"]
        purchased = 0

    total = included + purchased
    return {
        "included": included,
        "purchased": purchased,
        "total": total,
        "used": used_count,
        "remaining": max(total - used_count, 0),
        "unlimited": False,
    }


async def check_avatar_slot_available(db: AsyncSession, owner_id: str) -> None:
    if await _is_admin(db, owner_id):
        return
    summary = await get_avatar_slot_summary(db, owner_id)
    if summary["used"] >= summary["total"]:
        raise HTTPException(
            status_code=402,
            detail=(
                f"You've used all {summary['total']} avatar slots on your plan. "
                "Upgrade your plan or purchase an additional avatar slot to continue."
            ),
        )


async def purchase_avatar_slot(
    db: AsyncSession, owner_id: str, quantity: int = 1, stripe_invoice_item_id: Optional[str] = None
) -> AvatarSlotPurchase:
    subscription = await get_active_subscription(db, owner_id)
    if not _is_billable(subscription):
        raise HTTPException(status_code=400, detail="An active subscription is required to buy extra avatar slots.")
    purchase = AvatarSlotPurchase(
        id=_id("asp"),
        user_id=owner_id,
        subscription_id=subscription.id,
        quantity=quantity,
        rate_cents_per_slot=OVERAGE_RATE_CENTS["avatar_slot_per_month"],
        stripe_invoice_item_id=stripe_invoice_item_id,
        active=True,
    )
    subscription.extra_avatar_slots = (subscription.extra_avatar_slots or 0) + quantity
    db.add(purchase)
    await db.commit()
    return purchase


# ── PAYG credit wallet ────────────────────────────────────────────────────


async def get_or_create_credit_wallet(db: AsyncSession, owner_id: str) -> CreditWallet:
    wallet = (
        await db.execute(select(CreditWallet).where(CreditWallet.user_id == owner_id))
    ).scalar_one_or_none()
    if wallet:
        return wallet
    wallet = CreditWallet(id=_id("wal"), user_id=owner_id, balance_cents=0)
    db.add(wallet)
    await db.commit()
    return wallet


async def _deduct_credits(
    db: AsyncSession,
    wallet: CreditWallet,
    amount_cents: int,
    *,
    description: str,
    related_render_id: Optional[str] = None,
    related_stream_session_id: Optional[str] = None,
) -> int:
    """FIFO-consume `amount_cents` across non-expired purchase batches.
    Caller must ensure `amount_cents <= wallet.balance_cents`. Writes one
    ledger row per batch drawn from, each linked via `related_purchase_id`.
    Returns the amount actually deducted (== amount_cents unless the
    caller over-requested, in which case it's capped to the balance)."""
    amount_cents = min(amount_cents, wallet.balance_cents)
    if amount_cents <= 0:
        return 0

    now = _naive_utc_now()
    batches = (
        await db.execute(
            select(CreditTransaction)
            .where(
                CreditTransaction.wallet_id == wallet.id,
                CreditTransaction.type.in_(
                    [CreditTransactionType.PURCHASE.value, CreditTransactionType.AUTO_TOPUP.value]
                ),
                CreditTransaction.remaining_cents > 0,
            )
            .order_by(CreditTransaction.expires_at.asc().nulls_last(), CreditTransaction.created_at.asc())
        )
    ).scalars().all()
    # Non-expired first (FIFO by expiry), skip anything already expired —
    # expire_credits() should have zeroed those out, but don't rely on the
    # nightly job having run yet.
    batches = [b for b in batches if not b.expires_at or b.expires_at > now]

    to_deduct = amount_cents
    for batch in batches:
        if to_deduct <= 0:
            break
        take = min(batch.remaining_cents, to_deduct)
        batch.remaining_cents -= take
        to_deduct -= take
        wallet.balance_cents -= take
        db.add(
            CreditTransaction(
                id=_id("ctx"),
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                type=CreditTransactionType.DEDUCTION.value,
                amount_cents=-take,
                balance_after_cents=wallet.balance_cents,
                description=description,
                related_purchase_id=batch.id,
                related_render_id=related_render_id,
                related_stream_session_id=related_stream_session_id,
            )
        )
    await db.commit()
    await maybe_auto_topup(db, wallet)
    return amount_cents - to_deduct


async def purchase_credits(
    db: AsyncSession,
    owner_id: str,
    pack_id: str,
    *,
    stripe_payment_intent_id: Optional[str] = None,
    stripe_checkout_session_id: Optional[str] = None,
    transaction_type: str = CreditTransactionType.PURCHASE.value,
) -> CreditTransaction:
    pack = CREDIT_PACKS.get(pack_id)
    if not pack:
        raise ValueError(f"Unknown credit pack: {pack_id}")
    wallet = await get_or_create_credit_wallet(db, owner_id)
    amount_cents = pack["amount_cents"]
    now = _naive_utc_now()
    expires_at = _add_months(now, CREDIT_EXPIRY_MONTHS)

    wallet.balance_cents += amount_cents
    tx = CreditTransaction(
        id=_id("ctx"),
        wallet_id=wallet.id,
        user_id=owner_id,
        type=transaction_type,
        amount_cents=amount_cents,
        balance_after_cents=wallet.balance_cents,
        description=f"Purchased ${amount_cents / 100:.2f} credit pack ({pack_id})",
        remaining_cents=amount_cents,
        expires_at=expires_at,
        stripe_payment_intent_id=stripe_payment_intent_id,
        stripe_checkout_session_id=stripe_checkout_session_id,
    )
    db.add(tx)
    await db.commit()
    return tx


async def record_subscription_payment(db: AsyncSession, invoice_obj: dict) -> Optional[SubscriptionPayment]:
    """Write a `SubscriptionPayment` ledger row from a Stripe `invoice.paid`
    event's data object.

    This is the only place subscription revenue actually gets recorded —
    `Subscription` only tracks current plan/period state and never stored a
    dollar amount. Best-effort: logs and returns None rather than raising,
    since the caller (the webhook handler) must still 200 the delivery even
    if this can't be resolved — Stripe has already been paid either way.
    """
    stripe_invoice_id = invoice_obj.get("id")
    if not stripe_invoice_id:
        return None

    amount_paid = invoice_obj.get("amount_paid") or 0
    if amount_paid <= 0:
        # Fully covered by a discount/credit note — no real cash collected.
        return None

    stripe_subscription_id = invoice_obj.get("subscription")
    stripe_customer_id = invoice_obj.get("customer")

    subscription = None
    if stripe_subscription_id:
        subscription = (
            await db.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
            )
        ).scalar_one_or_none()
    if subscription is None and stripe_customer_id:
        # Rare race: the very first invoice.paid for a brand-new
        # subscription can arrive before customer.subscription.created has
        # synced our Subscription row yet. Fall back to matching on the
        # Stripe customer id.
        subscription = (
            await db.execute(
                select(Subscription).where(Subscription.stripe_customer_id == stripe_customer_id)
            )
        ).scalar_one_or_none()

    if subscription is None:
        logger.warning(
            "invoice.paid %s: could not resolve a Subscription for stripe_subscription_id=%s customer=%s — skipping",
            stripe_invoice_id, stripe_subscription_id, stripe_customer_id,
        )
        return None

    lines = (invoice_obj.get("lines") or {}).get("data") or []
    period = (lines[0].get("period") if lines else None) or {}

    payment = SubscriptionPayment(
        id=_id("subp"),
        user_id=subscription.user_id,
        subscription_id=subscription.id,
        stripe_invoice_id=stripe_invoice_id,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        amount_cents=amount_paid,
        currency=invoice_obj.get("currency"),
        billing_reason=invoice_obj.get("billing_reason"),
        period_start=stripe_timestamp_to_naive_utc(period.get("start")),
        period_end=stripe_timestamp_to_naive_utc(period.get("end")),
    )
    db.add(payment)
    try:
        await db.commit()
    except IntegrityError:
        # stripe_invoice_id unique constraint — this invoice was already
        # recorded (e.g. a redelivery under a different Stripe event id
        # than the one ProcessedStripeEvent already deduped). Not an error.
        await db.rollback()
        logger.info("invoice.paid %s already recorded — skipping duplicate", stripe_invoice_id)
        return None
    return payment


async def expire_credits(db: AsyncSession, *, now: Optional[datetime] = None) -> dict:
    """Zero out any purchase batch past its 12-month expiry, writing an
    `expiration` ledger row for the forfeited amount. Intended to run daily
    via Celery beat."""
    now = now or _naive_utc_now()
    batches = (
        await db.execute(
            select(CreditTransaction).where(
                CreditTransaction.type.in_(
                    [CreditTransactionType.PURCHASE.value, CreditTransactionType.AUTO_TOPUP.value]
                ),
                CreditTransaction.remaining_cents > 0,
                CreditTransaction.expires_at.isnot(None),
                CreditTransaction.expires_at <= now,
            )
        )
    ).scalars().all()

    expired_total = 0
    wallets_touched: dict[str, CreditWallet] = {}
    for batch in batches:
        if batch.wallet_id not in wallets_touched:
            wallet = await db.get(CreditWallet, batch.wallet_id)
            wallets_touched[batch.wallet_id] = wallet
        wallet = wallets_touched[batch.wallet_id]

        forfeited = batch.remaining_cents
        batch.remaining_cents = 0
        wallet.balance_cents = max(wallet.balance_cents - forfeited, 0)
        expired_total += forfeited
        db.add(
            CreditTransaction(
                id=_id("ctx"),
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                type=CreditTransactionType.EXPIRATION.value,
                amount_cents=-forfeited,
                balance_after_cents=wallet.balance_cents,
                description=f"Expired unused credits from purchase {batch.id}",
                related_purchase_id=batch.id,
                expires_at=None,
            )
        )
    await db.commit()
    return {"batches_expired": len(batches), "cents_expired": expired_total}


async def set_auto_topup(
    db: AsyncSession,
    owner_id: str,
    *,
    enabled: bool,
    threshold_cents: Optional[int] = None,
    amount_cents: Optional[int] = None,
) -> CreditWallet:
    wallet = await get_or_create_credit_wallet(db, owner_id)
    wallet.auto_topup_enabled = enabled
    if threshold_cents is not None:
        wallet.auto_topup_threshold_cents = threshold_cents
    if amount_cents is not None:
        wallet.auto_topup_amount_cents = amount_cents
    await db.commit()
    return wallet


async def maybe_auto_topup(db: AsyncSession, wallet: CreditWallet) -> bool:
    """Best-effort: if auto-top-up is enabled and the balance has dropped
    below the configured threshold, charge the saved payment method for
    the configured top-up amount and credit the wallet. Never raises —
    a failed auto-charge just leaves the balance low (still visible on the
    dashboard) rather than breaking whatever triggered the deduction."""
    if not wallet.auto_topup_enabled:
        return False
    threshold = wallet.auto_topup_threshold_cents or 0
    if wallet.balance_cents > threshold:
        return False
    amount_cents = wallet.auto_topup_amount_cents or CREDIT_PACKS["credits_20"]["amount_cents"]
    if not wallet.stripe_payment_method_id:
        logger.info("Auto-top-up skipped for %s: no saved payment method", wallet.user_id)
        return False
    try:
        from services.stripe_billing import get_stripe_billing_service

        stripe_service = get_stripe_billing_service()
        subscription = await get_active_subscription(db, wallet.user_id)
        customer_id = subscription.stripe_customer_id if subscription else None
        if not customer_id:
            return False
        intent = await stripe_service.create_off_session_payment(
            amount_cents=amount_cents,
            customer_id=customer_id,
            payment_method_id=wallet.stripe_payment_method_id,
            metadata={"owner_id": wallet.user_id, "kind": "auto_topup"},
        )
        now = _naive_utc_now()
        expires_at = _add_months(now, CREDIT_EXPIRY_MONTHS)
        wallet.balance_cents += amount_cents
        db.add(
            CreditTransaction(
                id=_id("ctx"),
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                type=CreditTransactionType.AUTO_TOPUP.value,
                amount_cents=amount_cents,
                balance_after_cents=wallet.balance_cents,
                description=f"Auto-top-up (${amount_cents / 100:.2f})",
                remaining_cents=amount_cents,
                expires_at=expires_at,
                stripe_payment_intent_id=intent.get("id"),
            )
        )
        await db.commit()
        return True
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("Auto-top-up failed for %s: %s", wallet.user_id, exc)
        return False


# ── Subscription lifecycle ───────────────────────────────────────────────


async def create_or_update_subscription_from_stripe(
    db: AsyncSession,
    *,
    owner_id: str,
    plan: str,
    interval: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    stripe_price_id: Optional[str],
    current_period_start: datetime,
    current_period_end: datetime,
) -> Subscription:
    """Create-or-update, called from the Stripe webhook handler for
    `checkout.session.completed` / `customer.subscription.updated`. Always
    logs a `SubscriptionEvent` describing what changed — created, upgraded,
    downgraded, or renewed — so history survives even though `Subscription`
    itself only holds current state."""
    existing = await get_active_subscription(db, owner_id)

    if not existing:
        sub = Subscription(
            id=_id("sub"),
            user_id=owner_id,
            plan=plan,
            interval=interval,
            status=SubscriptionStatus.ACTIVE.value,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_price_id=stripe_price_id,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        db.add(sub)
        db.add(
            SubscriptionEvent(
                id=_id("sev"),
                subscription_id=sub.id,
                user_id=owner_id,
                event_type=SubscriptionEventType.CREATED.value,
                to_plan=plan,
                to_interval=interval,
            )
        )
        await db.commit()
        return sub

    event_type = None
    if existing.plan != plan:
        event_type = (
            SubscriptionEventType.UPGRADED.value
            if _PLAN_RANK.get(plan, 0) > _PLAN_RANK.get(existing.plan, 0)
            else SubscriptionEventType.DOWNGRADED.value
        )
    elif existing.current_period_start < current_period_start:
        event_type = SubscriptionEventType.RENEWED.value
    elif existing.status != SubscriptionStatus.ACTIVE.value:
        event_type = SubscriptionEventType.REACTIVATED.value

    if event_type:
        db.add(
            SubscriptionEvent(
                id=_id("sev"),
                subscription_id=existing.id,
                user_id=owner_id,
                event_type=event_type,
                from_plan=existing.plan,
                to_plan=plan,
                from_interval=existing.interval,
                to_interval=interval,
            )
        )

    existing.plan = plan
    existing.interval = interval
    existing.status = SubscriptionStatus.ACTIVE.value
    existing.stripe_customer_id = stripe_customer_id
    existing.stripe_subscription_id = stripe_subscription_id
    existing.stripe_price_id = stripe_price_id
    existing.current_period_start = current_period_start
    existing.current_period_end = current_period_end
    existing.cancel_at_period_end = False
    existing.canceled_at = None
    await db.commit()
    return existing


async def mark_subscription_cancel_at_period_end(db: AsyncSession, owner_id: str) -> Optional[Subscription]:
    sub = await get_active_subscription(db, owner_id)
    if not sub:
        return None
    sub.cancel_at_period_end = True
    sub.canceled_at = _naive_utc_now()
    db.add(
        SubscriptionEvent(
            id=_id("sev"), subscription_id=sub.id, user_id=owner_id,
            event_type=SubscriptionEventType.CANCELED.value, from_plan=sub.plan,
            note="cancel_at_period_end",
        )
    )
    await db.commit()
    return sub


async def mark_subscription_terminated(db: AsyncSession, owner_id: str) -> Optional[Subscription]:
    """Stripe `customer.subscription.deleted` — subscription is gone now,
    not just scheduled to cancel."""
    sub = await get_active_subscription(db, owner_id)
    if not sub:
        return None
    sub.status = SubscriptionStatus.CANCELED.value
    sub.canceled_at = sub.canceled_at or _naive_utc_now()
    db.add(
        SubscriptionEvent(
            id=_id("sev"), subscription_id=sub.id, user_id=owner_id,
            event_type=SubscriptionEventType.CANCELED.value, from_plan=sub.plan,
        )
    )
    await db.commit()
    return sub


async def mark_subscription_past_due(db: AsyncSession, owner_id: str) -> Optional[Subscription]:
    sub = await get_active_subscription(db, owner_id)
    if not sub:
        return None
    sub.status = SubscriptionStatus.PAST_DUE.value
    db.add(
        SubscriptionEvent(
            id=_id("sev"), subscription_id=sub.id, user_id=owner_id,
            event_type=SubscriptionEventType.PAYMENT_FAILED.value, from_plan=sub.plan,
        )
    )
    await db.commit()
    return sub


# ── Dashboard aggregation ────────────────────────────────────────────────


def _describe_next_render_billing(
    subscription: Optional[Subscription], period, wallet, is_admin: bool = False
) -> dict:
    """How the NEXT render will be paid for — so the UI can warn the user
    BEFORE a render that will charge their card as overage (there is no other
    prompt anywhere; the current flow just fires an off-session charge per
    render — see _attempt_overage_charge). Mirrors the cascade used by
    check_render_preflight + deduct_render_usage: included -> credits ->
    overage / blocked.

    Bug: check_render_preflight and deduct_render_usage were both fixed to
    never block/charge an admin, but this preview (what the editor page's
    "Finalize & Render" confirmation reads) independently recomputed the
    same cascade with no admin awareness — an admin WITH an active
    subscription record (e.g. one who was a paying customer before being
    made admin) whose included minutes were used up would still see "this
    render will be charged to your card", even though it genuinely
    wouldn't be. `is_admin` short-circuits straight to a clean, accurate
    "included" / no-charge result.
    """
    if is_admin:
        return {
            "source": "included",
            "will_charge_card": False,
            "overage_rate_cents_per_minute": {
                "standard": get_overage_render_rate_cents("standard"),
                "premium": get_overage_render_rate_cents("premium"),
            },
            "non_subscriber_rate_cents_per_minute": (
                NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE
            ),
        }

    remaining = max(
        period.render_minutes_included - period.render_minutes_used, 0.0
    )
    billable = _is_billable(subscription)
    has_card = bool(subscription and subscription.stripe_customer_id)

    if remaining > 0 and (billable or period.is_free_tier):
        source = "included"
    elif wallet.balance_cents > 0:
        source = "credits"
    elif billable:
        source = "overage"
    else:
        source = "blocked"

    return {
        "source": source,
        # True only when the next render triggers an immediate off-session
        # card charge (active subscription + a saved card on the customer).
        "will_charge_card": source == "overage" and has_card,
        "overage_rate_cents_per_minute": {
            "standard": get_overage_render_rate_cents("standard"),
            "premium": get_overage_render_rate_cents("premium"),
        },
        "non_subscriber_rate_cents_per_minute": (
            NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE
        ),
    }


async def get_billing_dashboard(db: AsyncSession, owner_id: str) -> dict:
    subscription = await get_active_subscription(db, owner_id)
    period = await get_or_create_current_usage_period(db, owner_id)
    wallet = await get_or_create_credit_wallet(db, owner_id)
    avatar_summary = await get_avatar_slot_summary(db, owner_id)
    is_admin = await _is_admin(db, owner_id)

    plan_id = subscription.plan if _is_billable(subscription) else "free"
    return {
        "plan": plan_id,
        "interval": subscription.interval if subscription else None,
        "status": subscription.status if subscription else ("free" if period.is_free_tier else None),
        "cancel_at_period_end": bool(subscription.cancel_at_period_end) if subscription else False,
        "renewal_date": subscription.current_period_end.isoformat() if _is_billable(subscription) else None,
        "is_free_tier": period.is_free_tier,
        "render_minutes": {
            "included": period.render_minutes_included,
            "used": period.render_minutes_used,
            "remaining": max(period.render_minutes_included - period.render_minutes_used, 0),
        },
        "live_stream_hours": {
            "included": period.live_stream_hours_included,
            "used": period.live_stream_hours_used,
            "remaining": max(period.live_stream_hours_included - period.live_stream_hours_used, 0),
        },
        "avatar_slots": avatar_summary,
        "credits": {
            "balance_cents": wallet.balance_cents,
            "auto_topup_enabled": wallet.auto_topup_enabled,
            "auto_topup_threshold_cents": wallet.auto_topup_threshold_cents,
            "auto_topup_amount_cents": wallet.auto_topup_amount_cents,
        },
        "render_billing": _describe_next_render_billing(subscription, period, wallet, is_admin),
    }
