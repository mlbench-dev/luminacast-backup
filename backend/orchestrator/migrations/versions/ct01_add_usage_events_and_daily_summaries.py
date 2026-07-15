"""add usage_events and usage_daily_summaries

Revision ID: ct01_usage_events
Revises: ph56_ci_wt
Create Date: 2026-05-04

Foundation for cost tracking (PR α). Tables are created here; the
event-logging instrumentation lands in PR β and the dashboards/rollup in
PR γ.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "ct01_usage_events"
down_revision: Union[str, None] = "ph56_ci_wt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=True),
        sa.Column("resource_type", sa.String(30), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("render_id", sa.String(), nullable=True),
        sa.Column("block_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(30), nullable=True),
        sa.Column("provider_cost_usd", sa.Float(), server_default="0.0"),
        sa.Column("user_price_usd", sa.Float(), server_default="0.0"),
        sa.Column("quantity", sa.Float(), server_default="1.0"),
        sa.Column("quantity_unit", sa.String(20), server_default="call"),
        sa.Column("provider_job_id", sa.String(100), nullable=True),
        sa.Column("provider_model", sa.String(100), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_event_type", "usage_events", ["event_type"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])

    op.create_table(
        "usage_daily_summaries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("total_provider_cost", sa.Float(), server_default="0.0"),
        sa.Column("total_user_price", sa.Float(), server_default="0.0"),
        sa.Column("render_count", sa.Integer(), server_default="0"),
        sa.Column("render_cost", sa.Float(), server_default="0.0"),
        sa.Column("render_gpu_seconds", sa.Float(), server_default="0.0"),
        sa.Column("script_gen_count", sa.Integer(), server_default="0"),
        sa.Column("script_gen_cost", sa.Float(), server_default="0.0"),
        sa.Column("script_gen_tokens", sa.Integer(), server_default="0"),
        sa.Column("tts_count", sa.Integer(), server_default="0"),
        sa.Column("tts_cost", sa.Float(), server_default="0.0"),
        sa.Column("music_count", sa.Integer(), server_default="0"),
        sa.Column("music_cost", sa.Float(), server_default="0.0"),
        sa.Column("body_shot_count", sa.Integer(), server_default="0"),
        sa.Column("body_shot_cost", sa.Float(), server_default="0.0"),
        sa.Column("publish_count", sa.Integer(), server_default="0"),
        sa.Column("publish_cost", sa.Float(), server_default="0.0"),
        sa.Column("storage_bytes_added", sa.BigInteger(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_usage_daily_summaries_user_id", "usage_daily_summaries", ["user_id"])
    op.create_index("ix_usage_daily_summaries_date", "usage_daily_summaries", ["date"])


def downgrade() -> None:
    op.drop_index("ix_usage_daily_summaries_date", table_name="usage_daily_summaries")
    op.drop_index("ix_usage_daily_summaries_user_id", table_name="usage_daily_summaries")
    op.drop_table("usage_daily_summaries")
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_event_type", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_table("usage_events")
