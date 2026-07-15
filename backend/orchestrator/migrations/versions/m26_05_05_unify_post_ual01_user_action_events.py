"""create user_action_events table — user action audit log

Revision ID: m26_05_05_unify_post_ual01
Revises: m26_05_05_unify
Create Date: 2026-05-05

Adds the append-only `user_action_events` table used by the platform-wide
user action audit log. See services/audit_log.py for the helper that writes
rows here, and api/history.py for the read endpoints.

NOTE: The alembic graph on this repo has been fragile — the deploy pipeline
falls back to raw-SQL DDL when alembic upgrade can't find a clean path.
The PR description includes the equivalent psql DDL for that fallback.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "m26_05_05_unify_post_ual01"
down_revision: Union[str, Sequence[str], None] = "m26_05_05_unify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_action_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=40),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=80), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column(
            "cast_id",
            sa.String(length=40),
            sa.ForeignKey("casts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("before", postgresql.JSONB, nullable=True),
        sa.Column("after", postgresql.JSONB, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_uae_user_created", "user_action_events", ["user_id", "created_at"])
    op.create_index("ix_uae_cast_created", "user_action_events", ["cast_id", "created_at"])
    op.create_index("ix_uae_action", "user_action_events", ["action"])
    op.create_index("ix_uae_entity", "user_action_events", ["entity_type", "entity_id"])
    op.create_index("ix_user_action_events_user_id", "user_action_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_action_events_user_id", table_name="user_action_events")
    op.drop_index("ix_uae_entity", table_name="user_action_events")
    op.drop_index("ix_uae_action", table_name="user_action_events")
    op.drop_index("ix_uae_cast_created", table_name="user_action_events")
    op.drop_index("ix_uae_user_created", table_name="user_action_events")
    op.drop_table("user_action_events")
