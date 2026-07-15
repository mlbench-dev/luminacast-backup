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


class StripeBillingService:
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY

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
    ) -> dict:
        """Create Checkout Session. Returns {"url": str, "id": str}."""
        _log(
            "info",
            "stripe_billing",
            "Creating checkout session",
            customer_id=customer_id,
        )

        session = await _retry_async(
            self._run_in_executor,
            stripe.checkout.Session.create,
            customer=customer_id,
            line_items=line_items,
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
        )

        _log(
            "info",
            "stripe_billing",
            "Checkout session created",
            session_id=session["id"],
        )
        return {"url": session["url"], "id": session["id"]}

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
