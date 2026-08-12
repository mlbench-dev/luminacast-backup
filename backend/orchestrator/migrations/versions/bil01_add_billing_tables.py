"""subscriptions + PAYG credits + usage-metering tables

Revision ID: bil01_add_billing_tables
Revises: m29_unify_heads
Create Date: 2026-08-12

Adds the full billing/metering schema described in services/billing.py's
models: subscriptions, usage_periods, render_usage_records,
live_stream_usage_records, avatar_slot_purchases, credit_wallets,
credit_transactions, overage_charges, subscription_events,
processed_stripe_events. All brand new tables (no pre-existing drift risk
to guard against, unlike m28's ALTERs of long-lived tables).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bil01_add_billing_tables"
down_revision: Union[str, None] = "m29_unify_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "subscriptions" not in existing_tables:
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("plan", sa.String(20), nullable=False),
            sa.Column("interval", sa.String(10), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("stripe_customer_id", sa.String(), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(), nullable=True),
            sa.Column("stripe_price_id", sa.String(), nullable=True),
            sa.Column("current_period_start", sa.DateTime(), nullable=False),
            sa.Column("current_period_end", sa.DateTime(), nullable=False),
            sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.false()),
            sa.Column("canceled_at", sa.DateTime(), nullable=True),
            sa.Column("extra_avatar_slots", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=True)
        op.create_index("ix_subscriptions_stripe_customer_id", "subscriptions", ["stripe_customer_id"])
        op.create_index(
            "ix_subscriptions_stripe_subscription_id", "subscriptions", ["stripe_subscription_id"], unique=True
        )

    if "usage_periods" not in existing_tables:
        op.create_table(
            "usage_periods",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("subscription_id", sa.String(), sa.ForeignKey("subscriptions.id"), nullable=True),
            sa.Column("period_start", sa.DateTime(), nullable=False),
            sa.Column("period_end", sa.DateTime(), nullable=True),
            sa.Column("is_free_tier", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("plan", sa.String(20), nullable=False),
            sa.Column("render_minutes_included", sa.Float(), nullable=False, server_default="0"),
            sa.Column("render_minutes_used", sa.Float(), nullable=False, server_default="0"),
            sa.Column("live_stream_hours_included", sa.Float(), nullable=False, server_default="0"),
            sa.Column("live_stream_hours_used", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_usage_periods_user_id", "usage_periods", ["user_id"])
        op.create_index("ix_usage_periods_subscription_id", "usage_periods", ["subscription_id"])
        op.create_index("ix_usage_periods_user_current", "usage_periods", ["user_id", "period_start"])

    if "render_usage_records" not in existing_tables:
        op.create_table(
            "render_usage_records",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("render_id", sa.String(), sa.ForeignKey("cast_renders.id"), nullable=False),
            sa.Column("cast_id", sa.String(), sa.ForeignKey("casts.id"), nullable=True),
            sa.Column("usage_period_id", sa.String(), sa.ForeignKey("usage_periods.id"), nullable=True),
            sa.Column("duration_seconds", sa.Float(), nullable=False),
            sa.Column("production_level", sa.String(20), nullable=False),
            sa.Column("multiplier", sa.Float(), nullable=False),
            sa.Column("billable_minutes", sa.Float(), nullable=False),
            sa.Column("included_minutes_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credits_minutes_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credits_amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("overage_minutes_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("overage_rate_cents_per_minute", sa.Integer(), nullable=True),
            sa.Column("overage_amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("free_minutes_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_render_usage_records_user_id", "render_usage_records", ["user_id"])
        op.create_index(
            "ix_render_usage_records_render_id", "render_usage_records", ["render_id"], unique=True
        )
        op.create_index("ix_render_usage_records_cast_id", "render_usage_records", ["cast_id"])
        op.create_index(
            "ix_render_usage_records_usage_period_id", "render_usage_records", ["usage_period_id"]
        )

    if "live_stream_usage_records" not in existing_tables:
        op.create_table(
            "live_stream_usage_records",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "stream_session_id", sa.String(), sa.ForeignKey("stream_sessions.id"), nullable=False
            ),
            sa.Column("usage_period_id", sa.String(), sa.ForeignKey("usage_periods.id"), nullable=True),
            sa.Column("duration_minutes", sa.Float(), nullable=False),
            sa.Column("billable_hours", sa.Float(), nullable=False),
            sa.Column("included_hours_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credits_hours_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credits_amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("overage_hours_applied", sa.Float(), nullable=False, server_default="0"),
            sa.Column("overage_rate_cents_per_hour", sa.Integer(), nullable=True),
            sa.Column("overage_amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_live_stream_usage_records_user_id", "live_stream_usage_records", ["user_id"])
        op.create_index(
            "ix_live_stream_usage_records_stream_session_id",
            "live_stream_usage_records",
            ["stream_session_id"],
            unique=True,
        )
        op.create_index(
            "ix_live_stream_usage_records_usage_period_id", "live_stream_usage_records", ["usage_period_id"]
        )

    if "avatar_slot_purchases" not in existing_tables:
        op.create_table(
            "avatar_slot_purchases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("subscription_id", sa.String(), sa.ForeignKey("subscriptions.id"), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("rate_cents_per_slot", sa.Integer(), nullable=False),
            sa.Column("stripe_invoice_item_id", sa.String(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_avatar_slot_purchases_user_id", "avatar_slot_purchases", ["user_id"])
        op.create_index(
            "ix_avatar_slot_purchases_subscription_id", "avatar_slot_purchases", ["subscription_id"]
        )

    if "credit_wallets" not in existing_tables:
        op.create_table(
            "credit_wallets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("balance_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("auto_topup_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("auto_topup_threshold_cents", sa.Integer(), nullable=True),
            sa.Column("auto_topup_amount_cents", sa.Integer(), nullable=True),
            sa.Column("stripe_payment_method_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_credit_wallets_user_id", "credit_wallets", ["user_id"], unique=True)

    if "credit_transactions" not in existing_tables:
        op.create_table(
            "credit_transactions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("wallet_id", sa.String(), sa.ForeignKey("credit_wallets.id"), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("type", sa.String(20), nullable=False),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("balance_after_cents", sa.Integer(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("remaining_cents", sa.Integer(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column(
                "related_purchase_id", sa.String(), sa.ForeignKey("credit_transactions.id"), nullable=True
            ),
            sa.Column("related_render_id", sa.String(), sa.ForeignKey("cast_renders.id"), nullable=True),
            sa.Column(
                "related_stream_session_id",
                sa.String(),
                sa.ForeignKey("stream_sessions.id"),
                nullable=True,
            ),
            sa.Column("stripe_payment_intent_id", sa.String(), nullable=True),
            sa.Column("stripe_checkout_session_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_credit_transactions_wallet_id", "credit_transactions", ["wallet_id"])
        op.create_index("ix_credit_transactions_user_id", "credit_transactions", ["user_id"])
        op.create_index(
            "ix_credit_transactions_expires_at", "credit_transactions", ["wallet_id", "expires_at"]
        )

    if "overage_charges" not in existing_tables:
        op.create_table(
            "overage_charges",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("usage_period_id", sa.String(), sa.ForeignKey("usage_periods.id"), nullable=True),
            sa.Column("resource_type", sa.String(20), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("rate_cents", sa.Integer(), nullable=False),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("billing_method", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("stripe_payment_intent_id", sa.String(), nullable=True),
            sa.Column("related_render_id", sa.String(), sa.ForeignKey("cast_renders.id"), nullable=True),
            sa.Column(
                "related_stream_session_id",
                sa.String(),
                sa.ForeignKey("stream_sessions.id"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_overage_charges_user_id", "overage_charges", ["user_id"])
        op.create_index("ix_overage_charges_usage_period_id", "overage_charges", ["usage_period_id"])

    if "subscription_events" not in existing_tables:
        op.create_table(
            "subscription_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("subscription_id", sa.String(), sa.ForeignKey("subscriptions.id"), nullable=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("event_type", sa.String(20), nullable=False),
            sa.Column("from_plan", sa.String(20), nullable=True),
            sa.Column("to_plan", sa.String(20), nullable=True),
            sa.Column("from_interval", sa.String(10), nullable=True),
            sa.Column("to_interval", sa.String(10), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("stripe_event_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_subscription_events_subscription_id", "subscription_events", ["subscription_id"])
        op.create_index("ix_subscription_events_user_id", "subscription_events", ["user_id"])
        op.create_index("ix_subscription_events_stripe_event_id", "subscription_events", ["stripe_event_id"])

    if "processed_stripe_events" not in existing_tables:
        op.create_table(
            "processed_stripe_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("processed_at", sa.DateTime(), server_default=sa.text("now()")),
        )


def downgrade() -> None:
    for table in (
        "processed_stripe_events",
        "subscription_events",
        "overage_charges",
        "credit_transactions",
        "credit_wallets",
        "avatar_slot_purchases",
        "live_stream_usage_records",
        "render_usage_records",
        "usage_periods",
        "subscriptions",
    ):
        op.drop_table(table)
