"""Subscription + PAYG credits + usage-metering API.

Every endpoint here is workspace-owner-scoped (`Depends(require_owner)`,
same as `routers/teams.py`) — billing belongs to whoever owns the
workspace, never to a team member acting inside it. Nothing here lets a
client set its own balance/allowance/plan directly: writes only ever go
through `services/billing_service.py`, and subscription state is only
ever mutated by the Stripe webhook handler (`routers/webhooks.py`) or by
an explicit action here that itself calls Stripe first.
"""
import logging

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.billing import CreditTransaction, SubscriptionStatus
from models.user import User
from routers.auth import WorkspaceContext, require_owner
from schemas.billing import (
    AutoTopupRequest,
    CreditsCheckoutRequest,
    PortalRequest,
    SubscriptionCheckoutRequest,
)
from services import billing_service
from services.billing_config import (
    CREDIT_PACKS,
    FREE_TIER,
    OVERAGE_RATE_CENTS,
    PLAN_CATALOG,
    STARTER_FAIR_USE_SOCIAL_ACCOUNTS,
)
from services.stripe_billing import get_stripe_billing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/plans")
async def list_plans():
    """Public catalog for the pricing page — no auth required."""
    return {
        "plans": PLAN_CATALOG,
        "free_tier": FREE_TIER,
        "credit_packs": CREDIT_PACKS,
        "overage_rates": OVERAGE_RATE_CENTS,
        "starter_fair_use_social_accounts": STARTER_FAIR_USE_SOCIAL_ACCOUNTS,
    }


@router.get("/dashboard")
async def get_dashboard(
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    return await billing_service.get_billing_dashboard(db, ctx.workspace_owner_id)


async def _get_or_create_stripe_customer(db: AsyncSession, user: User) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    stripe_service = get_stripe_billing_service()
    customer = await stripe_service.create_customer(email=user.email, user_id=user.id)
    user.stripe_customer_id = customer["id"]
    await db.commit()
    return customer["id"]


@router.post("/checkout/subscription")
async def checkout_subscription(
    req: SubscriptionCheckoutRequest,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    plan = PLAN_CATALOG.get(req.plan)
    if not plan:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {req.plan}")
    if req.interval not in ("month", "year"):
        raise HTTPException(status_code=400, detail="interval must be 'month' or 'year'")

    price_id = getattr(settings, plan["stripe_price_id_env"][req.interval], "")
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"Stripe price for {req.plan}/{req.interval} is not configured yet.",
        )

    # Checkout Sessions in `mode="subscription"` always create a brand-new
    # Stripe Subscription — Stripe has no idea an owner might already have
    # one. Without this guard, clicking "Choose Starter" a second time (or
    # a double-click, or "Change plan" from the dashboard while already on
    # a plan) silently creates a second, parallel subscription that bills
    # independently of the first.
    existing = await billing_service.get_active_subscription(db, ctx.workspace_owner_id)
    if existing and existing.status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.PAST_DUE.value):
        if existing.plan == req.plan and existing.interval == req.interval:
            raise HTTPException(
                status_code=409,
                detail=f"You're already subscribed to {plan['name']} ({req.interval}ly). "
                "Manage your subscription from the billing portal instead.",
            )
        # Different plan/interval while already subscribed: change the
        # existing Stripe subscription in place (prorated) rather than
        # opening a second checkout — this is what "upgrade"/"downgrade"
        # means, not "buy a second subscription".
        stripe_service = get_stripe_billing_service()
        try:
            updated = await stripe_service.update_subscription_price(
                existing.stripe_subscription_id, price_id
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise HTTPException(status_code=502, detail="Could not change your plan. Please try again.")
        sub = await billing_service.create_or_update_subscription_from_stripe(
            db,
            owner_id=ctx.workspace_owner_id,
            plan=req.plan,
            interval=req.interval,
            stripe_customer_id=updated.get("customer") or existing.stripe_customer_id,
            stripe_subscription_id=updated["id"],
            stripe_price_id=price_id,
            current_period_start=billing_service.stripe_timestamp_to_naive_utc(
                updated["items"]["data"][0].get("current_period_start")
            ),
            current_period_end=billing_service.stripe_timestamp_to_naive_utc(
                updated["items"]["data"][0].get("current_period_end")
            ),
        )
        return {
            "status": "updated",
            "plan": sub.plan,
            "interval": sub.interval,
            "renewal_date": sub.current_period_end.isoformat(),
        }

    user = await db.get(User, ctx.workspace_owner_id)
    customer_id = await _get_or_create_stripe_customer(db, user)

    stripe_service = get_stripe_billing_service()
    try:
        session = await stripe_service.create_checkout_session(
            customer_id=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{settings.FRONTEND_URL.rstrip('/')}/settings/billing?checkout=success",
            cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/settings/pricing?checkout=cancelled",
            metadata={"owner_id": ctx.workspace_owner_id, "plan": req.plan, "interval": req.interval},
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return session


@router.post("/checkout/credits")
async def checkout_credits(
    req: CreditsCheckoutRequest,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    pack = CREDIT_PACKS.get(req.pack_id)
    if not pack:
        raise HTTPException(status_code=400, detail=f"Unknown credit pack: {req.pack_id}")

    price_id = getattr(settings, pack["stripe_price_id_env"], "")
    if not price_id:
        raise HTTPException(
            status_code=503, detail=f"Stripe price for {req.pack_id} is not configured yet."
        )

    user = await db.get(User, ctx.workspace_owner_id)
    customer_id = await _get_or_create_stripe_customer(db, user)

    stripe_service = get_stripe_billing_service()
    try:
        session = await stripe_service.create_checkout_session(
            customer_id=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",
            success_url=f"{settings.FRONTEND_URL.rstrip('/')}/settings/billing?credits=success",
            cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/settings/billing?credits=cancelled",
            metadata={"owner_id": ctx.workspace_owner_id, "pack_id": req.pack_id},
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return session


@router.post("/portal")
async def billing_portal(
    req: PortalRequest,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, ctx.workspace_owner_id)
    if not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account yet — subscribe to a plan first.")
    stripe_service = get_stripe_billing_service()
    try:
        session = await stripe_service.create_billing_portal_session(
            customer_id=user.stripe_customer_id,
            return_url=req.return_url or f"{settings.FRONTEND_URL.rstrip('/')}/settings/billing",
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=502, detail="Could not open billing portal. Please try again.")
    return session


@router.post("/cancel")
async def cancel_subscription(
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    subscription = await billing_service.get_active_subscription(db, ctx.workspace_owner_id)
    if not subscription or not subscription.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription to cancel.")
    stripe_service = get_stripe_billing_service()
    try:
        await stripe_service.cancel_subscription(subscription.stripe_subscription_id, at_period_end=True)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=502, detail="Could not cancel subscription. Please try again.")
    updated = await billing_service.mark_subscription_cancel_at_period_end(db, ctx.workspace_owner_id)
    return {
        "status": "scheduled",
        "cancel_at_period_end": True,
        "renewal_date": updated.current_period_end.isoformat() if updated else None,
    }


@router.post("/avatar-slots/purchase")
async def purchase_avatar_slot(
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    subscription = await billing_service.get_active_subscription(db, ctx.workspace_owner_id)
    if not subscription or not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=400, detail="An active subscription with billing on file is required."
        )
    rate_cents = OVERAGE_RATE_CENTS["avatar_slot_per_month"]
    stripe_service = get_stripe_billing_service()

    # create_off_session_payment only attaches a payment_method to the
    # PaymentIntent when one is explicitly passed — otherwise Stripe falls
    # back to the CUSTOMER's default_payment_method, which is a distinct
    # field from the SUBSCRIPTION's own default_payment_method and isn't
    # reliably set even for an actively-paying customer (confirmed live:
    # cus_V4XToZl9rdarcK has an active, successfully-billed Studio
    # subscription, yet this charge failed with Stripe's
    # payment_intent_unexpected_state — "missing a payment method" — purely
    # because the customer-level default was never set). The subscription's
    # own default_payment_method is what actually charges every renewal, so
    # resolve and use THAT instead of relying on the customer-level default.
    payment_method_id = None
    if subscription.stripe_subscription_id:
        try:
            stripe_sub = await stripe_service.get_subscription(subscription.stripe_subscription_id)
            pm = stripe_sub.get("default_payment_method")
            payment_method_id = pm.get("id") if isinstance(pm, dict) else pm
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

    try:
        intent = await stripe_service.create_off_session_payment(
            amount_cents=rate_cents,
            customer_id=subscription.stripe_customer_id,
            payment_method_id=payment_method_id,
            metadata={"owner_id": ctx.workspace_owner_id, "kind": "avatar_slot"},
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            status_code=402, detail="Could not charge your card for the additional avatar slot."
        )
    purchase = await billing_service.purchase_avatar_slot(
        db, ctx.workspace_owner_id, quantity=1, stripe_invoice_item_id=intent.get("id")
    )
    return {"avatar_slot_purchase_id": purchase.id, "rate_cents": rate_cents}


@router.get("/credits/transactions")
async def list_credit_transactions(
    limit: int = 50,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    rows = (
        await db.execute(
            select(CreditTransaction)
            .where(CreditTransaction.user_id == ctx.workspace_owner_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "transactions": [
            {
                "id": t.id,
                "type": t.type,
                "amount_cents": t.amount_cents,
                "balance_after_cents": t.balance_after_cents,
                "description": t.description,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ]
    }


@router.post("/credits/auto-topup")
async def update_auto_topup(
    req: AutoTopupRequest,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    wallet = await billing_service.set_auto_topup(
        db,
        ctx.workspace_owner_id,
        enabled=req.enabled,
        threshold_cents=req.threshold_cents,
        amount_cents=req.amount_cents,
    )
    return {
        "auto_topup_enabled": wallet.auto_topup_enabled,
        "auto_topup_threshold_cents": wallet.auto_topup_threshold_cents,
        "auto_topup_amount_cents": wallet.auto_topup_amount_cents,
    }
