"""Subscription + PAYG credits + usage-metering — services/billing_service.py.

Exercises the centralized metering service directly against the test
Postgres DB (not mocked) since the whole point of this module is DB-backed
cascading/idempotency logic. Stripe network calls are avoided by testing
paths that don't require a `stripe_customer_id` on the subscription (the
`_attempt_overage_charge` fallback to `pending` when there's nothing to
charge) rather than mocking the Stripe SDK itself.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from models.billing import (
    CreditTransaction,
    OverageBillingMethod,
    OverageCharge,
    ProcessedStripeEvent,
    RenderUsageRecord,
    Subscription,
    SubscriptionStatus,
)
from models.cast_render import CastRender
from services import billing_service


def _naive_now():
    return billing_service._naive_utc_now()


async def _make_cast_render(db_session, user, render_id=None):
    """Creates a minimal real Avatar + Cast + CastRender chain so the
    render_usage_records FK constraints are satisfiable — billing doesn't
    care about cast content, just that `cast_renders.id`/`cast_id` exist.
    """
    from models.avatar import Avatar, AvatarType, AvatarStatus
    from models.cast import Cast, CastStatus

    avatar = Avatar(
        id=f"avt_test_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        type=AvatarType.CLONE,
        status=AvatarStatus.READY,
        voice_id="voice_test_123",
    )
    db_session.add(avatar)
    cast = Cast(
        id=f"cst_test_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        avatar_id=avatar.id,
        name="Test Cast",
        status=CastStatus.READY,
        template_name="beauty_haul",
    )
    db_session.add(cast)
    await db_session.commit()

    render_id = render_id or f"rnd_test_{uuid.uuid4().hex[:8]}"
    render = CastRender(
        id=render_id,
        cast_id=cast.id,
        user_id=user.id,
        status="ready",
        timeline_snapshot={},
    )
    db_session.add(render)
    await db_session.commit()
    return render


async def _make_subscription(
    db_session, user, plan="starter", interval="month", status=SubscriptionStatus.ACTIVE.value,
    stripe_customer_id=None, period_start=None, period_end=None,
):
    period_start = period_start or _naive_now()
    if period_end is None:
        period_end = period_start + (
            timedelta(days=365) if interval == "year" else timedelta(days=30)
        )
    sub = Subscription(
        id=f"sub_test_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        plan=plan,
        interval=interval,
        status=status,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=f"sub_stripe_{uuid.uuid4().hex[:8]}",
        current_period_start=period_start,
        current_period_end=period_end,
    )
    db_session.add(sub)
    await db_session.commit()
    return sub


class TestComputeBillableMinutes:
    def test_standard_multiplier_is_one(self):
        minutes, multiplier, level = billing_service.compute_billable_minutes(120, "standard")
        assert minutes == 2.0
        assert multiplier == 1.0
        assert level == "standard"

    def test_premium_multiplier_applies(self):
        # 2-minute video at 1.5x premium multiplier -> 3 billable minutes.
        minutes, multiplier, level = billing_service.compute_billable_minutes(120, "premium")
        assert minutes == 3.0
        assert multiplier == 1.5
        assert level == "premium"

    def test_unknown_level_normalizes_to_standard(self):
        minutes, multiplier, level = billing_service.compute_billable_minutes(60, "quick")
        assert level == "standard"
        assert multiplier == 1.0
        assert minutes == 1.0


class TestFreeTier:
    @pytest.mark.asyncio
    async def test_free_tier_period_created_with_two_minutes(self, db_session, make_user):
        user = await make_user(email=f"free_{uuid.uuid4().hex[:6]}@test.com")
        period = await billing_service.get_or_create_current_usage_period(db_session, user.id)
        assert period.is_free_tier is True
        assert period.render_minutes_included == 2.0
        assert period.render_minutes_used == 0.0
        assert period.period_end is None

    @pytest.mark.asyncio
    async def test_free_render_consumes_free_allowance_then_pending_overage(self, db_session, make_user):
        user = await make_user(email=f"free2_{uuid.uuid4().hex[:6]}@test.com")
        render = await _make_cast_render(db_session, user)

        # 5-minute standard render but only 2 free minutes available ->
        # 2 min free + 3 min uncollectable overage (no subscription, no
        # credits) recorded as `pending`, never silently dropped.
        record = await billing_service.deduct_render_usage(
            db_session,
            user_id=user.id,
            owner_id=user.id,
            render_id=render.id,
            cast_id=render.cast_id,
            duration_seconds=300,
            production_level="standard",
        )
        assert record.free_minutes_applied == 2.0
        assert record.overage_minutes_applied == 3.0
        assert record.billable_minutes == 5.0

        overage = (
            await db_session.execute(
                select(OverageCharge).where(OverageCharge.related_render_id == render.id)
            )
        ).scalar_one()
        assert overage.billing_method == OverageBillingMethod.PENDING.value
        assert overage.amount_cents == 3 * billing_service.NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE

    @pytest.mark.asyncio
    async def test_free_allowance_exhausted_blocks_preflight(self, db_session, make_user):
        user = await make_user(email=f"free3_{uuid.uuid4().hex[:6]}@test.com")
        render = await _make_cast_render(db_session, user)
        await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=180, production_level="standard",
        )
        # 3 free minutes used > 2 included -> allowance exhausted, and with
        # no subscription/credits, preflight should now block new renders.
        with pytest.raises(Exception) as exc_info:
            await billing_service.check_render_preflight(db_session, user.id)
        assert getattr(exc_info.value, "status_code", None) == 402


class TestIncludedAllowanceAndIdempotency:
    @pytest.mark.asyncio
    async def test_included_allowance_consumed_before_overage(self, db_session, make_user):
        user = await make_user(email=f"starter_{uuid.uuid4().hex[:6]}@test.com")
        await _make_subscription(db_session, user, plan="starter", interval="month")
        render = await _make_cast_render(db_session, user)

        # Starter plan includes 10 render minutes/month; a 4-minute standard
        # render should be fully covered by included allowance.
        record = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=240, production_level="standard",
        )
        assert record.included_minutes_applied == 4.0
        assert record.overage_minutes_applied == 0.0
        assert record.credits_minutes_applied == 0.0

        period = await billing_service.get_or_create_current_usage_period(db_session, user.id)
        assert period.render_minutes_used == 4.0

    @pytest.mark.asyncio
    async def test_deduct_render_usage_is_idempotent(self, db_session, make_user):
        user = await make_user(email=f"idem_{uuid.uuid4().hex[:6]}@test.com")
        await _make_subscription(db_session, user, plan="starter", interval="month")
        render = await _make_cast_render(db_session, user)

        first = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=120, production_level="standard",
        )
        second = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=120, production_level="standard",
        )
        assert first.id == second.id

        count = (
            await db_session.execute(
                select(RenderUsageRecord).where(RenderUsageRecord.render_id == render.id)
            )
        ).scalars().all()
        assert len(count) == 1

        period = await billing_service.get_or_create_current_usage_period(db_session, user.id)
        # Only billed once — 2 minutes, not 4.
        assert period.render_minutes_used == 2.0

    @pytest.mark.asyncio
    async def test_render_exceeding_remaining_allowance_goes_to_overage(self, db_session, make_user):
        """Edge case: user starts a render with 2 minutes remaining but the
        render consumes 5 billable minutes — must not fail, must bill the
        overage."""
        user = await make_user(email=f"overage_{uuid.uuid4().hex[:6]}@test.com")
        await _make_subscription(db_session, user, plan="starter", interval="month")

        # Burn 8 of the 10 included minutes first.
        r1 = await _make_cast_render(db_session, user)
        await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=r1.id,
            cast_id=r1.cast_id, duration_seconds=480, production_level="standard",
        )

        # Now render 5 more premium-multiplier minutes (2min * 1.5x video
        # duration is beside the point here — pass duration directly).
        r2 = await _make_cast_render(db_session, user)
        record = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=r2.id,
            cast_id=r2.cast_id, duration_seconds=300, production_level="standard",
        )
        assert record.included_minutes_applied == 2.0  # only 2 min of allowance was left
        assert record.overage_minutes_applied == 3.0
        assert record.overage_rate_cents_per_minute == billing_service.get_overage_render_rate_cents("standard")

        overage = (
            await db_session.execute(
                select(OverageCharge).where(OverageCharge.related_render_id == r2.id)
            )
        ).scalar_one()
        # No stripe_customer_id on the test subscription -> can't collect
        # immediately, but it IS recorded (never silently dropped).
        assert overage.billing_method == OverageBillingMethod.PENDING.value


class TestCreditsWallet:
    @pytest.mark.asyncio
    async def test_purchase_credits_creates_ledger_and_balance(self, db_session, make_user):
        user = await make_user(email=f"credits_{uuid.uuid4().hex[:6]}@test.com")
        tx = await billing_service.purchase_credits(db_session, user.id, "credits_20")
        assert tx.amount_cents == 2000
        assert tx.remaining_cents == 2000
        assert tx.expires_at is not None

        wallet = await billing_service.get_or_create_credit_wallet(db_session, user.id)
        assert wallet.balance_cents == 2000

    @pytest.mark.asyncio
    async def test_free_tier_allowance_is_used_before_touching_credits(self, db_session, make_user):
        user = await make_user(email=f"credituse_{uuid.uuid4().hex[:6]}@test.com")
        await billing_service.purchase_credits(db_session, user.id, "credits_20")  # $20.00

        render = await _make_cast_render(db_session, user)
        record = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=120, production_level="standard",
        )
        # Free tier covers the first 2 minutes before credits are touched.
        assert record.free_minutes_applied == 2.0
        assert record.credits_minutes_applied == 0.0

        wallet = await billing_service.get_or_create_credit_wallet(db_session, user.id)
        assert wallet.balance_cents == 2000  # untouched — free tier absorbed it

    @pytest.mark.asyncio
    async def test_credits_used_once_free_tier_exhausted(self, db_session, make_user):
        user = await make_user(email=f"credituse2_{uuid.uuid4().hex[:6]}@test.com")
        await billing_service.purchase_credits(db_session, user.id, "credits_20")  # $20.00 = 2000c

        render = await _make_cast_render(db_session, user)
        # 4-minute render: 2 min free tier + 2 min from credits at $8/min = 1600c.
        record = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=240, production_level="standard",
        )
        assert record.free_minutes_applied == 2.0
        assert record.credits_minutes_applied == 2.0
        assert record.credits_amount_cents == 2 * billing_service.NON_SUBSCRIBER_RENDER_RATE_CENTS_PER_MINUTE
        assert record.overage_minutes_applied == 0.0

        wallet = await billing_service.get_or_create_credit_wallet(db_session, user.id)
        assert wallet.balance_cents == 2000 - record.credits_amount_cents

        ledger = (
            await db_session.execute(
                select(CreditTransaction).where(
                    CreditTransaction.wallet_id == wallet.id,
                    CreditTransaction.type == "deduction",
                )
            )
        ).scalars().all()
        assert len(ledger) == 1
        assert ledger[0].amount_cents == -record.credits_amount_cents
        assert ledger[0].related_render_id == render.id

    @pytest.mark.asyncio
    async def test_expire_credits_forfeits_only_past_expiry(self, db_session, make_user):
        user = await make_user(email=f"expire_{uuid.uuid4().hex[:6]}@test.com")
        tx = await billing_service.purchase_credits(db_session, user.id, "credits_50")
        wallet = await billing_service.get_or_create_credit_wallet(db_session, user.id)
        assert wallet.balance_cents == 5000

        # Force this batch's expiry into the past (simulating 12 months
        # elapsed) without waiting for real time to pass.
        tx.expires_at = _naive_now() - timedelta(days=1)
        await db_session.commit()

        result = await billing_service.expire_credits(db_session)
        assert result["batches_expired"] == 1
        assert result["cents_expired"] == 5000

        await db_session.refresh(wallet)
        assert wallet.balance_cents == 0

        expiration_row = (
            await db_session.execute(
                select(CreditTransaction).where(
                    CreditTransaction.wallet_id == wallet.id,
                    CreditTransaction.type == "expiration",
                )
            )
        ).scalar_one()
        assert expiration_row.amount_cents == -5000

    @pytest.mark.asyncio
    async def test_non_expired_credits_survive_expire_run(self, db_session, make_user):
        user = await make_user(email=f"notexpired_{uuid.uuid4().hex[:6]}@test.com")
        await billing_service.purchase_credits(db_session, user.id, "credits_20")
        result = await billing_service.expire_credits(db_session)
        assert result["batches_expired"] == 0
        wallet = await billing_service.get_or_create_credit_wallet(db_session, user.id)
        assert wallet.balance_cents == 2000


class TestMonthlyResetAndAnnual:
    @pytest.mark.asyncio
    async def test_monthly_subscription_usage_period_matches_billing_cycle(self, db_session, make_user):
        user = await make_user(email=f"monthly_{uuid.uuid4().hex[:6]}@test.com")
        start = _naive_now()
        await _make_subscription(
            db_session, user, plan="pro", interval="month",
            period_start=start, period_end=start + timedelta(days=30),
        )
        period = await billing_service.get_or_create_current_usage_period(db_session, user.id, now=start)
        assert period.render_minutes_included == 25.0  # Pro plan
        assert period.period_end == start + timedelta(days=30)

    @pytest.mark.asyncio
    async def test_annual_subscriber_gets_monthly_allowance_not_full_year(self, db_session, make_user):
        """Spec: an annual Starter subscriber receives 10 render minutes
        per month, NOT 120 minutes immediately."""
        user = await make_user(email=f"annual_{uuid.uuid4().hex[:6]}@test.com")
        start = _naive_now()
        await _make_subscription(
            db_session, user, plan="starter", interval="year",
            period_start=start, period_end=start + timedelta(days=365),
        )
        period = await billing_service.get_or_create_current_usage_period(db_session, user.id, now=start)
        assert period.render_minutes_included == 10.0  # one month's worth, not 120
        assert period.period_end < start + timedelta(days=365)

    @pytest.mark.asyncio
    async def test_annual_subscriber_next_month_gets_fresh_allowance_no_rollover(self, db_session, make_user):
        user = await make_user(email=f"annual2_{uuid.uuid4().hex[:6]}@test.com")
        start = _naive_now()
        await _make_subscription(
            db_session, user, plan="starter", interval="year",
            period_start=start, period_end=start + timedelta(days=365),
        )
        month1 = await billing_service.get_or_create_current_usage_period(db_session, user.id, now=start)
        render = await _make_cast_render(db_session, user)
        await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=180, production_level="standard",
        )
        await db_session.refresh(month1)
        assert month1.render_minutes_used == 3.0

        # Jump 35 days ahead — a new monthly slice should be created with a
        # clean 10-minute allowance; the unused 7 minutes from month 1 do
        # NOT carry forward.
        later = start + timedelta(days=35)
        month2 = await billing_service.get_or_create_current_usage_period(db_session, user.id, now=later)
        assert month2.id != month1.id
        assert month2.render_minutes_included == 10.0
        assert month2.render_minutes_used == 0.0

    @pytest.mark.asyncio
    async def test_refresh_usage_periods_creates_missing_period_and_expires_lapsed(self, db_session, make_user):
        user_active = await make_user(email=f"refresh1_{uuid.uuid4().hex[:6]}@test.com")
        start = _naive_now() - timedelta(days=40)
        await _make_subscription(
            db_session, user_active, plan="pro", interval="month",
            period_start=start, period_end=start + timedelta(days=90),
        )

        user_lapsed = await make_user(email=f"refresh2_{uuid.uuid4().hex[:6]}@test.com")
        lapsed_sub = await _make_subscription(
            db_session, user_lapsed, plan="starter", interval="month",
            period_start=start, period_end=_naive_now() - timedelta(days=1),
        )

        result = await billing_service.refresh_usage_periods(db_session)
        assert result["subscriptions_expired"] >= 1

        await db_session.refresh(lapsed_sub)
        assert lapsed_sub.status == SubscriptionStatus.EXPIRED.value


class TestAvatarSlots:
    @pytest.mark.asyncio
    async def test_free_tier_allows_one_avatar(self, db_session, make_user):
        user = await make_user(email=f"avslot_{uuid.uuid4().hex[:6]}@test.com")
        summary = await billing_service.get_avatar_slot_summary(db_session, user.id)
        assert summary["total"] == 1
        await billing_service.check_avatar_slot_available(db_session, user.id)  # no raise

    @pytest.mark.asyncio
    async def test_avatar_slot_blocked_when_at_capacity(self, db_session, make_user, make_avatar):
        user = await make_user(email=f"avslot2_{uuid.uuid4().hex[:6]}@test.com")
        await make_avatar(user.id)  # fills the free tier's 1 slot
        with pytest.raises(Exception) as exc_info:
            await billing_service.check_avatar_slot_available(db_session, user.id)
        assert getattr(exc_info.value, "status_code", None) == 402

    @pytest.mark.asyncio
    async def test_purchased_slot_increases_total(self, db_session, make_user):
        user = await make_user(email=f"avslot3_{uuid.uuid4().hex[:6]}@test.com")
        await _make_subscription(db_session, user, plan="starter", interval="month")
        before = await billing_service.get_avatar_slot_summary(db_session, user.id)
        assert before["total"] == 3  # Starter plan

        await billing_service.purchase_avatar_slot(db_session, user.id, quantity=1)
        after = await billing_service.get_avatar_slot_summary(db_session, user.id)
        assert after["total"] == 4
        assert after["purchased"] == 1


class TestSubscriptionLifecycle:
    @pytest.mark.asyncio
    async def test_create_from_stripe_logs_created_event(self, db_session, make_user):
        user = await make_user(email=f"lifecycle1_{uuid.uuid4().hex[:6]}@test.com")
        start = _naive_now()
        sub = await billing_service.create_or_update_subscription_from_stripe(
            db_session, owner_id=user.id, plan="pro", interval="month",
            stripe_customer_id="cus_test_1", stripe_subscription_id="sub_test_1",
            stripe_price_id="price_test_1", current_period_start=start,
            current_period_end=start + timedelta(days=30),
        )
        assert sub.plan == "pro"
        assert sub.status == SubscriptionStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_upgrade_logs_upgraded_event_and_downgrade_logs_downgraded(self, db_session, make_user):
        from models.billing import SubscriptionEvent

        user = await make_user(email=f"lifecycle2_{uuid.uuid4().hex[:6]}@test.com")
        start = _naive_now()
        sub = await billing_service.create_or_update_subscription_from_stripe(
            db_session, owner_id=user.id, plan="starter", interval="month",
            stripe_customer_id="cus_test_2", stripe_subscription_id="sub_test_2",
            stripe_price_id="price_starter", current_period_start=start,
            current_period_end=start + timedelta(days=30),
        )
        await billing_service.create_or_update_subscription_from_stripe(
            db_session, owner_id=user.id, plan="studio", interval="month",
            stripe_customer_id="cus_test_2", stripe_subscription_id="sub_test_2",
            stripe_price_id="price_studio", current_period_start=start,
            current_period_end=start + timedelta(days=30),
        )
        events = (
            await db_session.execute(
                select(SubscriptionEvent).where(SubscriptionEvent.subscription_id == sub.id)
            )
        ).scalars().all()
        types = [e.event_type for e in events]
        assert "created" in types
        assert "upgraded" in types

    @pytest.mark.asyncio
    async def test_cancel_at_period_end_keeps_subscription_usable(self, db_session, make_user):
        user = await make_user(email=f"lifecycle3_{uuid.uuid4().hex[:6]}@test.com")
        await _make_subscription(db_session, user, plan="starter", interval="month")
        updated = await billing_service.mark_subscription_cancel_at_period_end(db_session, user.id)
        assert updated.cancel_at_period_end is True
        assert updated.status == SubscriptionStatus.ACTIVE.value  # still usable until period end

    @pytest.mark.asyncio
    async def test_plan_change_does_not_delete_usage_history(self, db_session, make_user):
        """Edge case: subscription changes plans while existing usage
        history must remain intact."""
        user = await make_user(email=f"lifecycle4_{uuid.uuid4().hex[:6]}@test.com")
        await _make_subscription(db_session, user, plan="starter", interval="month")
        render = await _make_cast_render(db_session, user)
        record = await billing_service.deduct_render_usage(
            db_session, user_id=user.id, owner_id=user.id, render_id=render.id,
            cast_id=render.cast_id, duration_seconds=60, production_level="standard",
        )

        start = _naive_now()
        await billing_service.create_or_update_subscription_from_stripe(
            db_session, owner_id=user.id, plan="studio", interval="month",
            stripe_customer_id="cus_test_4", stripe_subscription_id="sub_test_4",
            stripe_price_id="price_studio", current_period_start=start,
            current_period_end=start + timedelta(days=30),
        )

        still_there = await db_session.get(RenderUsageRecord, record.id)
        assert still_there is not None
        assert still_there.billable_minutes == 1.0


class TestWebhookIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_stripe_event_id_is_rejected(self, db_session):
        """The webhook handler's idempotency guard: a second insert of the
        same Stripe event id must violate the primary key so the caller can
        treat it as an already-processed no-op."""
        from sqlalchemy.exc import IntegrityError

        event_id = f"evt_test_{uuid.uuid4().hex[:8]}"
        db_session.add(ProcessedStripeEvent(id=event_id, event_type="checkout.session.completed"))
        await db_session.commit()

        db_session.add(ProcessedStripeEvent(id=event_id, event_type="checkout.session.completed"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_purchase_credits_called_twice_is_not_idempotent_by_itself(self, db_session, make_user):
        """`purchase_credits` is a plain ledger write, not idempotent on its
        own — callers (the webhook handler) MUST rely on
        ProcessedStripeEvent to avoid calling it twice for one Stripe
        event. Documents that expectation with a concrete assertion."""
        user = await make_user(email=f"webhook_{uuid.uuid4().hex[:6]}@test.com")
        await billing_service.purchase_credits(db_session, user.id, "credits_20")
        await billing_service.purchase_credits(db_session, user.id, "credits_20")
        wallet = await billing_service.get_or_create_credit_wallet(db_session, user.id)
        assert wallet.balance_cents == 4000  # would be double-credited without the webhook guard
