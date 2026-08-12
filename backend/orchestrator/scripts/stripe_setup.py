"""Create the Stripe Products/Prices required by the subscription + PAYG
credits billing system (services/billing_config.py).

Run once per Stripe mode (test and live are separate — run it twice):

    docker compose exec orchestrator python scripts/stripe_setup.py

Idempotent: looks up existing Products by a stable `metadata.slug` before
creating, and existing recurring/one-time Prices by matching
unit_amount + interval on that product, so re-running never creates
duplicates.

Prints a `.env` block with the resulting Price IDs — paste those into the
deploy environment's `STRIPE_PRICE_*` variables (see config.py). Nothing
here writes to the local database; this only talks to Stripe.
"""
import os
import sys

import stripe

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402
from services.billing_config import CREDIT_PACKS, PLAN_CATALOG  # noqa: E402


def _find_or_create_product(slug: str, name: str) -> str:
    existing = stripe.Product.search(query=f"metadata['slug']:'{slug}'")
    if existing.data:
        return existing.data[0].id
    product = stripe.Product.create(name=name, metadata={"slug": slug})
    return product.id


def _find_or_create_recurring_price(product_id: str, amount_cents: int, interval: str) -> str:
    prices = stripe.Price.list(product=product_id, active=True, limit=100)
    for p in prices.data:
        if (
            p.unit_amount == amount_cents
            and p.recurring
            and p.recurring.interval == interval
        ):
            return p.id
    price = stripe.Price.create(
        product=product_id,
        unit_amount=amount_cents,
        currency="usd",
        recurring={"interval": interval},
    )
    return price.id


def _find_or_create_one_time_price(product_id: str, amount_cents: int) -> str:
    prices = stripe.Price.list(product=product_id, active=True, limit=100)
    for p in prices.data:
        if p.unit_amount == amount_cents and not p.recurring:
            return p.id
    price = stripe.Price.create(product=product_id, unit_amount=amount_cents, currency="usd")
    return price.id


def main() -> None:
    if not settings.STRIPE_SECRET_KEY:
        print("STRIPE_SECRET_KEY is not set — nothing to do.")
        return
    stripe.api_key = settings.STRIPE_SECRET_KEY

    env_lines = []

    for plan_id, plan in PLAN_CATALOG.items():
        product_id = _find_or_create_product(f"plan_{plan_id}", f"Luminacast {plan['name']}")
        monthly_price_id = _find_or_create_recurring_price(
            product_id, plan["monthly_price_cents"], "month"
        )
        annual_price_id = _find_or_create_recurring_price(
            product_id, plan["annual_price_cents"], "year"
        )
        env_lines.append(f"{plan['stripe_price_id_env']['month']}={monthly_price_id}")
        env_lines.append(f"{plan['stripe_price_id_env']['year']}={annual_price_id}")

    credits_product_id = _find_or_create_product("payg_credits", "Luminacast PAYG Credits")
    for pack_id, pack in CREDIT_PACKS.items():
        price_id = _find_or_create_one_time_price(credits_product_id, pack["amount_cents"])
        env_lines.append(f"{pack['stripe_price_id_env']}={price_id}")

    print("\n# Paste into the deploy environment (.env):")
    print("\n".join(env_lines))


if __name__ == "__main__":
    main()
