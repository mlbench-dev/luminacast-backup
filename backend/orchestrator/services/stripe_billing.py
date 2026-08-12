"""Stripe billing service adapter."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from functools import partial
from typing import Optional

import stripe

from config import settings

logger = logging.getLogger(__name__)


def _log(level: str, service: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": service,
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


async def _retry_async(func, *args, max_retries=3, base_delay=2.0, **kwargs):
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            _log(
                "warning",
                func.__module__ or "service",
                f"Retry {attempt + 1}/{max_retries}: {e}",
                delay=delay,
            )
            await asyncio.sleep(delay)


STRIPE_API_VERSION = "2026-07-29.dahlia"


class StripeBillingService:
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe.api_version = STRIPE_API_VERSION

    async def _run_in_executor(self, func, *args, **kwargs):
        """Run a synchronous Stripe call in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def create_customer(self, email: str, user_id: str) -> dict:
        """Create Stripe customer. Returns {"id": str}."""
        _log("info", "stripe_billing", "Creating customer", user_id=user_id, email=email)

        customer = await _retry_async(
            self._run_in_executor,
            stripe.Customer.create,
            email=email,
            metadata={"user_id": user_id},
        )

        _log(
            "info",
            "stripe_billing",
            "Customer created",
            user_id=user_id,
            customer_id=customer["id"],
        )
        return {"id": customer["id"]}

    async def create_payment_intent(
        self,
        amount_cents: int,
        customer_id: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Create PaymentIntent. Returns {"id": str, "status": str, "client_secret": str, "amount": int}."""
        _log(
            "info",
            "stripe_billing",
            "Creating payment intent",
            customer_id=customer_id,
            amount_cents=amount_cents,
        )

        intent = await _retry_async(
            self._run_in_executor,
            stripe.PaymentIntent.create,
            amount=amount_cents,
            currency="usd",
            customer=customer_id,
            metadata=metadata or {},
        )

        _log(
            "info",
            "stripe_billing",
            "Payment intent created",
            intent_id=intent["id"],
            status=intent["status"],
        )
        return {
            "id": intent["id"],
            "status": intent["status"],
            "client_secret": intent["client_secret"],
            "amount": intent["amount"],
        }

    async def report_usage(self, subscription_item_id: str, quantity: int) -> dict:
        """Report metered usage."""
        _log(
            "info",
            "stripe_billing",
            "Reporting metered usage",
            subscription_item_id=subscription_item_id,
            quantity=quantity,
        )

        record = await _retry_async(
            self._run_in_executor,
            stripe.SubscriptionItem.create_usage_record,
            subscription_item_id,
            quantity=quantity,
            action="increment",
        )

        _log(
            "info",
            "stripe_billing",
            "Usage reported",
            subscription_item_id=subscription_item_id,
            record_id=record["id"],
        )
        return dict(record)

    async def create_checkout_session(
        self,
        customer_id: str,
        line_items: list,
        success_url: str,
        cancel_url: str,
        mode: str = "payment",
        metadata: Optional[dict] = None,
    ) -> dict:
        """Create Checkout Session. `mode="subscription"` for plan
        checkout, `mode="payment"` (default) for one-time PAYG credit
        purchases. Returns {"url": str, "id": str}."""
        _log(
            "info",
            "stripe_billing",
            "Creating checkout session",
            customer_id=customer_id,
            mode=mode,
        )

        kwargs = dict(
            customer=customer_id,
            line_items=line_items,
            mode=mode,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata or {},
        )
        # A subscription Checkout Session should also save the card for
        # later off-session overage/auto-top-up charges. Metadata is set on
        # `subscription_data` too (not just the Session) so it survives
        # onto the Subscription object itself — `customer.subscription.*`
        # webhooks carry it directly without needing a Session lookup.
        if mode == "subscription":
            kwargs["payment_method_collection"] = "always"
            kwargs["subscription_data"] = {"metadata": metadata or {}}
        else:
            kwargs["payment_intent_data"] = {"setup_future_usage": "off_session"}

        session = await _retry_async(
            self._run_in_executor,
            stripe.checkout.Session.create,
            **kwargs,
        )

        _log(
            "info",
            "stripe_billing",
            "Checkout session created",
            session_id=session["id"],
        )
        return {"url": session["url"], "id": session["id"]}

    async def create_off_session_payment(
        self,
        amount_cents: int,
        customer_id: str,
        payment_method_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Charge a saved payment method without customer interaction —
        used for overage billing and auto-top-up. Raises on failure
        (card declined, no default payment method, etc.) — callers treat
        that as "could not collect" and record a `pending`/`failed`
        OverageCharge rather than blocking the usage that already happened.
        """
        _log(
            "info",
            "stripe_billing",
            "Creating off-session payment",
            customer_id=customer_id,
            amount_cents=amount_cents,
        )

        kwargs = dict(
            amount=amount_cents,
            currency="usd",
            customer=customer_id,
            off_session=True,
            confirm=True,
            metadata=metadata or {},
        )
        if payment_method_id:
            kwargs["payment_method"] = payment_method_id

        intent = await _retry_async(
            self._run_in_executor,
            stripe.PaymentIntent.create,
            max_retries=1,  # a declined/failed card won't succeed on retry
            **kwargs,
        )

        _log(
            "info",
            "stripe_billing",
            "Off-session payment created",
            intent_id=intent["id"],
            status=intent["status"],
        )
        return {"id": intent["id"], "status": intent["status"], "amount": intent["amount"]}

    async def get_subscription(self, subscription_id: str) -> dict:
        sub = await self._run_in_executor(stripe.Subscription.retrieve, subscription_id)
        return dict(sub)

    async def update_subscription_price(self, subscription_id: str, new_price_id: str) -> dict:
        """Change an existing subscription's plan in place (upgrade/downgrade)
        instead of starting a second, parallel subscription. Prorates the
        difference for the rest of the current billing period, matching how
        the Stripe-hosted billing portal's own plan-switcher behaves."""
        _log(
            "info", "stripe_billing", "Updating subscription price",
            subscription_id=subscription_id, new_price_id=new_price_id,
        )
        current = await self._run_in_executor(stripe.Subscription.retrieve, subscription_id)
        item_id = current["items"]["data"][0]["id"]
        updated = await self._run_in_executor(
            stripe.Subscription.modify,
            subscription_id,
            items=[{"id": item_id, "price": new_price_id}],
            proration_behavior="create_prorations",
        )
        return dict(updated)

    async def get_payment_intent(self, payment_intent_id: str) -> dict:
        intent = await self._run_in_executor(stripe.PaymentIntent.retrieve, payment_intent_id)
        return dict(intent)

    async def create_billing_portal_session(self, customer_id: str, return_url: str) -> dict:
        """Stripe-hosted portal for managing payment methods/invoices/
        cancellation. Returns {"url": str}."""
        session = await _retry_async(
            self._run_in_executor,
            stripe.billing_portal.Session.create,
            customer=customer_id,
            return_url=return_url,
        )
        return {"url": session["url"]}

    async def cancel_subscription(self, subscription_id: str, at_period_end: bool = True) -> dict:
        """Cancel a subscription. `at_period_end=True` (default) schedules
        cancellation for the end of the current billing period — usage
        already allocated for the period is not clawed back."""
        _log(
            "info", "stripe_billing", "Canceling subscription",
            subscription_id=subscription_id, at_period_end=at_period_end,
        )
        if at_period_end:
            sub = await self._run_in_executor(
                stripe.Subscription.modify, subscription_id, cancel_at_period_end=True
            )
        else:
            sub = await self._run_in_executor(stripe.Subscription.delete, subscription_id)
        return dict(sub)

    async def construct_webhook_event(self, payload: bytes, sig_header: str) -> dict:
        """Verify and construct webhook event."""
        _log("info", "stripe_billing", "Constructing webhook event")

        loop = asyncio.get_event_loop()
        event = await loop.run_in_executor(
            None,
            partial(
                stripe.Webhook.construct_event,
                payload,
                sig_header,
                settings.STRIPE_WEBHOOK_SECRET,
            ),
        )

        _log(
            "info",
            "stripe_billing",
            "Webhook event constructed",
            event_type=event.get("type"),
        )
        return dict(event)


_instance: StripeBillingService | None = None


def get_stripe_billing_service() -> StripeBillingService:
    global _instance
    if _instance is None:
        _instance = StripeBillingService()
    return _instance
