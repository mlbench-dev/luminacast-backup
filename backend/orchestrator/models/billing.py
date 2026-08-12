"""Subscription + PAYG credits + usage-metering models.

Billing is always scoped to a workspace OWNER (`WorkspaceContext.
workspace_owner_id`, see routers/auth.py), never to the acting team
member — a Pro/Studio plan's team seats are purchased by and billed to
the owner, matching how `routers/teams.py` already gates management
endpoints with `require_owner`.

Every usage deduction (`services/billing_service.py`) writes one
`RenderUsageRecord` / `LiveStreamUsageRecord` row per billable event,
carrying enough fields to answer "why was this charged what it was" on
its own — see each model's docstring. Nothing here mutates a single
running balance without leaving a row behind; `CreditWallet.balance_cents`
is a cache of the `CreditTransaction` ledger, not the source of truth.
"""
import enum

from database import Base
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


class Subscription(Base):
    """One row per workspace owner's current/most-recent Stripe subscription.

    History of *changes* (upgrade/downgrade/cancel/renew) lives in
    `SubscriptionEvent`, not here — this row always reflects current state
    only, matching how Stripe's own subscription object works.
    """

    __tablename__ = "subscriptions"
    id = Column(String, primary_key=True)  # prefix: sub_
    user_id = Column(String, ForeignKey("users.id"), unique=True, index=True, nullable=False)
    plan = Column(String(20), nullable=False)  # PlanTier value: starter/pro/studio
    interval = Column(String(10), nullable=False)  # BillingInterval value: month/year
    status = Column(String(20), nullable=False, default=SubscriptionStatus.ACTIVE.value)

    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, unique=True, index=True)
    stripe_price_id = Column(String, nullable=True)

    # The Stripe billing cycle — for annual plans this spans a full year;
    # monthly *usage* allowances are released in `UsagePeriod` slices inside
    # it (see billing_service.get_or_create_current_usage_period), not all
    # at once.
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    cancel_at_period_end = Column(Boolean, default=False)
    canceled_at = Column(DateTime, nullable=True)

    # Extra avatar slots purchased on top of the plan's included allowance,
    # at OVERAGE_RATE_CENTS["avatar_slot_per_month"] each. Avatar slots are
    # persistent, not a monthly credit, so this is a simple running count
    # rather than a per-period record.
    extra_avatar_slots = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    usage_periods = relationship("UsagePeriod", back_populates="subscription")


class UsagePeriod(Base):
    """One monthly allowance window for a workspace owner.

    Exists for both monthly AND annual subscribers — an annual subscriber's
    Stripe billing cycle is a year, but their render-minute/live-stream
    allowance is still released one `UsagePeriod` at a time, and unused
    allowance does not roll into the next one. `is_free_tier=True` rows
    represent the one-time (non-recurring) free allowance for owners with
    no active subscription; `period_end` is null for those since the free
    allowance doesn't reset.
    """

    __tablename__ = "usage_periods"
    id = Column(String, primary_key=True)  # prefix: usp_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    subscription_id = Column(String, ForeignKey("subscriptions.id"), index=True, nullable=True)

    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=True)  # null only for the free-tier row
    is_free_tier = Column(Boolean, nullable=False, default=False)

    plan = Column(String(20), nullable=False)  # snapshot: plan active during this period
    render_minutes_included = Column(Float, nullable=False, default=0)
    render_minutes_used = Column(Float, nullable=False, default=0)
    live_stream_hours_included = Column(Float, nullable=False, default=0)
    live_stream_hours_used = Column(Float, nullable=False, default=0)

    created_at = Column(DateTime, server_default=func.now())

    subscription = relationship("Subscription", back_populates="usage_periods")
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("ix_usage_periods_user_current", "user_id", "period_start"),
    )


class UsageSource(str, enum.Enum):
    FREE = "free"
    INCLUDED = "included"
    CREDITS = "credits"
    OVERAGE = "overage"


class RenderUsageRecord(Base):
    """One row per billable render — the audit trail for render metering.

    `render_id` carries a unique constraint so `deduct_render_usage` is
    idempotent: a render task retried after a worker crash (see the
    terminal-state guard in tasks/cast_render.py) cannot be billed twice.
    """

    __tablename__ = "render_usage_records"
    id = Column(String, primary_key=True)  # prefix: rur_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    render_id = Column(String, ForeignKey("cast_renders.id"), unique=True, index=True, nullable=False)
    cast_id = Column(String, ForeignKey("casts.id"), index=True, nullable=True)
    usage_period_id = Column(String, ForeignKey("usage_periods.id"), index=True, nullable=True)

    duration_seconds = Column(Float, nullable=False)
    production_level = Column(String(20), nullable=False)
    multiplier = Column(Float, nullable=False)
    billable_minutes = Column(Float, nullable=False)  # duration_minutes * multiplier

    # How the billable minutes were paid for. A single render can span
    # multiple sources (e.g. 2 min included + 3 min overage) — captured as
    # separate `_cents`/`_minutes` split fields rather than one enum, since
    # "source" isn't always singular.
    included_minutes_applied = Column(Float, nullable=False, default=0)
    credits_minutes_applied = Column(Float, nullable=False, default=0)
    credits_amount_cents = Column(Integer, nullable=False, default=0)
    overage_minutes_applied = Column(Float, nullable=False, default=0)
    overage_rate_cents_per_minute = Column(Integer, nullable=True)
    overage_amount_cents = Column(Integer, nullable=False, default=0)
    free_minutes_applied = Column(Float, nullable=False, default=0)

    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class LiveStreamUsageRecord(Base):
    """One row per completed live-stream session — mirrors RenderUsageRecord
    but tracked in hours, per the spec's requirement to keep live-stream
    usage separate from render usage."""

    __tablename__ = "live_stream_usage_records"
    id = Column(String, primary_key=True)  # prefix: lsu_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    stream_session_id = Column(
        String, ForeignKey("stream_sessions.id"), unique=True, index=True, nullable=False
    )
    usage_period_id = Column(String, ForeignKey("usage_periods.id"), index=True, nullable=True)

    duration_minutes = Column(Float, nullable=False)
    billable_hours = Column(Float, nullable=False)

    included_hours_applied = Column(Float, nullable=False, default=0)
    credits_hours_applied = Column(Float, nullable=False, default=0)
    credits_amount_cents = Column(Integer, nullable=False, default=0)
    overage_hours_applied = Column(Float, nullable=False, default=0)
    overage_rate_cents_per_hour = Column(Integer, nullable=True)
    overage_amount_cents = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class AvatarSlotPurchase(Base):
    """An add-on avatar slot purchased beyond the plan's included allowance.

    Avatar slots are persistent resources (spec: "not monthly credits, do
    not reset"), so this is a simple purchase log — the *effective* slot
    count is `plan.avatar_slots + sum(active AvatarSlotPurchase.quantity)`,
    computed in billing_service.get_avatar_slot_summary.
    """

    __tablename__ = "avatar_slot_purchases"
    id = Column(String, primary_key=True)  # prefix: asp_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    subscription_id = Column(String, ForeignKey("subscriptions.id"), index=True, nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    rate_cents_per_slot = Column(Integer, nullable=False)
    stripe_invoice_item_id = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class CreditWallet(Base):
    """One wallet per user. `balance_cents` is a maintained cache of the
    `CreditTransaction` ledger (every write to it happens inside the same
    DB transaction as the ledger row) so reads don't need to SUM every time,
    while the ledger remains the auditable source of truth."""

    __tablename__ = "credit_wallets"
    id = Column(String, primary_key=True)  # prefix: wal_
    user_id = Column(String, ForeignKey("users.id"), unique=True, index=True, nullable=False)
    balance_cents = Column(Integer, nullable=False, default=0)

    auto_topup_enabled = Column(Boolean, nullable=False, default=False)
    auto_topup_threshold_cents = Column(Integer, nullable=True)
    auto_topup_amount_cents = Column(Integer, nullable=True)
    # Stripe PaymentMethod id to charge off-session for overage/auto-topup.
    # Populated once the owner has completed a Checkout Session (Stripe
    # attaches the payment method to the Customer + we read it back on
    # `checkout.session.completed`).
    stripe_payment_method_id = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    user = relationship("User", foreign_keys=[user_id])


class CreditTransactionType(str, enum.Enum):
    PURCHASE = "purchase"
    AUTO_TOPUP = "auto_topup"
    DEDUCTION = "deduction"
    EXPIRATION = "expiration"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class CreditTransaction(Base):
    """Append-only ledger backing `CreditWallet.balance_cents`.

    `purchase`/`auto_topup` rows are FIFO expiration "batches": `amount_cents`
    is the original face value, `remaining_cents` is how much of that batch
    is still spendable, and `expires_at` is purchase time + 12 months.
    Deductions consume the oldest non-expired batch(es) first
    (`services/billing_service.py::_deduct_credits`). Rather than a join
    table, each deduction/expiration row links back to the single batch it
    drew down via `related_purchase_id` — a deduction spanning multiple
    batches writes one ledger row per batch, all sharing the same
    `description`/timestamp.
    """

    __tablename__ = "credit_transactions"
    id = Column(String, primary_key=True)  # prefix: ctx_
    wallet_id = Column(String, ForeignKey("credit_wallets.id"), index=True, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    type = Column(String(20), nullable=False)  # CreditTransactionType value

    amount_cents = Column(Integer, nullable=False)  # positive=credit, negative=debit
    balance_after_cents = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)

    # Purchase-batch bookkeeping (only set on purchase/auto_topup rows).
    remaining_cents = Column(Integer, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Deduction/expiration bookkeeping — which purchase batch this row drew
    # down or expired.
    related_purchase_id = Column(String, ForeignKey("credit_transactions.id"), nullable=True)
    related_render_id = Column(String, ForeignKey("cast_renders.id"), nullable=True)
    related_stream_session_id = Column(String, ForeignKey("stream_sessions.id"), nullable=True)

    stripe_payment_intent_id = Column(String, nullable=True)
    stripe_checkout_session_id = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class OverageBillingMethod(str, enum.Enum):
    CREDITS = "credits"
    STRIPE_CHARGE = "stripe_charge"
    PENDING = "pending"  # recorded, but no valid payment method to collect yet
    FAILED = "failed"


class OverageChargeResourceType(str, enum.Enum):
    RENDER = "render"
    LIVE_STREAM = "live_stream"
    AVATAR_SLOT = "avatar_slot"


class OverageCharge(Base):
    """One row per unit of usage billed beyond included allowance + credits.

    Written even when `billing_method=pending` (no charge attempted yet) or
    `failed` — the spec requires usage never silently exceed limits without
    a record of how it will be billed.
    """

    __tablename__ = "overage_charges"
    id = Column(String, primary_key=True)  # prefix: ovc_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    usage_period_id = Column(String, ForeignKey("usage_periods.id"), index=True, nullable=True)
    resource_type = Column(String(20), nullable=False)  # OverageChargeResourceType value
    quantity = Column(Float, nullable=False)  # minutes / hours / slot-months
    rate_cents = Column(Integer, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    billing_method = Column(String(20), nullable=False, default=OverageBillingMethod.PENDING.value)

    stripe_payment_intent_id = Column(String, nullable=True)
    related_render_id = Column(String, ForeignKey("cast_renders.id"), nullable=True)
    related_stream_session_id = Column(String, ForeignKey("stream_sessions.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class SubscriptionEventType(str, enum.Enum):
    CREATED = "created"
    UPGRADED = "upgraded"
    DOWNGRADED = "downgraded"
    RENEWED = "renewed"
    CANCELED = "canceled"
    REACTIVATED = "reactivated"
    EXPIRED = "expired"
    PAYMENT_FAILED = "payment_failed"


class SubscriptionEvent(Base):
    """Audit trail of every subscription state transition. Never deleted or
    rewritten when a subscription changes plan — usage history must stay
    intact, so this is append-only alongside `Subscription`'s mutable
    current-state row."""

    __tablename__ = "subscription_events"
    id = Column(String, primary_key=True)  # prefix: sev_
    subscription_id = Column(String, ForeignKey("subscriptions.id"), index=True, nullable=True)
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    event_type = Column(String(20), nullable=False)  # SubscriptionEventType value
    from_plan = Column(String(20), nullable=True)
    to_plan = Column(String(20), nullable=True)
    from_interval = Column(String(10), nullable=True)
    to_interval = Column(String(10), nullable=True)
    note = Column(Text, nullable=True)
    stripe_event_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class ProcessedStripeEvent(Base):
    """Idempotency guard for Stripe webhooks. `id` is the Stripe event id
    itself — a unique-constraint violation on insert means this event was
    already handled (duplicate delivery, or a retried delivery after our
    ack was lost), and the webhook handler treats that as an immediate,
    side-effect-free 200."""

    __tablename__ = "processed_stripe_events"
    id = Column(String, primary_key=True)  # the Stripe event id, e.g. evt_...
    event_type = Column(String(64), nullable=False)
    processed_at = Column(DateTime, server_default=func.now())
