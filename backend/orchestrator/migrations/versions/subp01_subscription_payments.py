"""subscription_payments table — real per-invoice Stripe revenue ledger

Revision ID: subp01_subscription_payments
Revises: pconn01_social_connect
Create Date: 2026-08-22

Part of fixing the admin Revenue dashboard: subscription payments were
never recorded anywhere (Subscription only tracks current plan/period
state, SubscriptionEvent only logs that a change happened, neither stores
an amount) — so subscription revenue, the platform's largest category,
was not computable from our own database at all. This table is populated
by a new `invoice.paid` Stripe webhook handler (routers/webhooks.py) going
forward. Table-existence guarded per this repo's convention (see
pconn01_social_connect.py, the previous head).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "subp01_subscription_payments"
down_revision: Union[str, None] = "pconn01_social_connect"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "subscription_payments" not in existing_tables:
        op.create_table(
            "subscription_payments",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("subscription_id", sa.String(), sa.ForeignKey("subscriptions.id"), nullable=True),
            sa.Column("stripe_invoice_id", sa.String(), nullable=False),
            sa.Column("stripe_customer_id", sa.String(), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(), nullable=True),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=True),
            sa.Column("billing_reason", sa.String(length=40), nullable=True),
            sa.Column("period_start", sa.DateTime(), nullable=True),
            sa.Column("period_end", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_subscription_payments_user_id", "subscription_payments", ["user_id"])
        op.create_index("ix_subscription_payments_subscription_id", "subscription_payments", ["subscription_id"])
        op.create_index(
            "ux_subscription_payments_stripe_invoice_id",
            "subscription_payments", ["stripe_invoice_id"], unique=True,
        )
        op.create_index("ix_subscription_payments_stripe_customer_id", "subscription_payments", ["stripe_customer_id"])
        op.create_index(
            "ix_subscription_payments_stripe_subscription_id", "subscription_payments", ["stripe_subscription_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "subscription_payments" in set(inspector.get_table_names()):
        op.drop_table("subscription_payments")
