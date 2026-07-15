"""add live_mode_defaults + user_video_ids to casts (PR #162 payload wiring)

Revision ID: chore_live_mode_payload
Revises: lref01_live_references
Create Date: 2026-06-17

PR #162 added the Stage-1 LIVE/Recorded toggle. The frontend POSTs
``live_mode_defaults`` (the LIVE preset object — voiceover/b-roll toggles, max
duration, b-roll cadence) and ``user_video_ids`` (list of UserVideoAsset ids the
user picked as preferred b-roll). The backend ignored both. This migration adds
the two nullable JSONB columns so they can be persisted; the route handlers read
them back into the outline generator and the b-roll pipeline.

Both columns are nullable with no server_default — a NULL value means "Recorded
cast / created before this column existed", which all existing reads tolerate.

Parented on ``lref01_live_references`` — the current single production head (it
unified the prior phantom heads add_uniq_cast_versions / d3e4f5a6b7c8 /
pri01_usage_vid_sec_note / td05_block_mic_on into one revision). Production has a
history of multiple open alembic heads; parenting on the unified head keeps this
migration on the single live branch. The main agent applies it manually via
psql; do NOT run ``alembic upgrade`` from here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "chore_live_mode_payload"
down_revision: Union[str, Sequence[str], None] = "lref01_live_references"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "casts",
        sa.Column("live_mode_defaults", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "casts",
        sa.Column("user_video_ids", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("casts", "user_video_ids")
    op.drop_column("casts", "live_mode_defaults")
