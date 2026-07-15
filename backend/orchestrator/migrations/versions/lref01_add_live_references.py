"""add live_references + live_reference_exemplars tables

Revision ID: lref01_live_references
Create Date: 2026-06-17

Production has multiple open alembic heads (the "phantom heads" problem). At
the time of writing the open heads were:
    add_uniq_cast_versions, d3e4f5a6b7c8, pri01_usage_vid_sec_note,
    td05_block_mic_on
Following the established repo pattern (see m24_merge_all_heads,
m26_05_05_unify_heads) this migration unifies them with a tuple
``down_revision`` so it becomes the single new head and creates the two
LiveReference tables in one shot. The main agent applies it manually via psql;
do NOT run ``alembic upgrade`` from here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "lref01_live_references"
down_revision: Union[str, Sequence[str], None] = (
    "add_uniq_cast_versions",
    "d3e4f5a6b7c8",
    "pri01_usage_vid_sec_note",
    "td05_block_mic_on",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_references",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("avatar_id", sa.String(), sa.ForeignKey("avatars.id"), nullable=True, index=True),
        sa.Column("cast_id", sa.String(), sa.ForeignKey("casts.id"), nullable=True, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("source_r2_key", sa.String(500), nullable=True),
        sa.Column("media_kind", sa.String(10), server_default="video", nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("transcript_segments", postgresql.JSONB(), nullable=True),
        sa.Column("assessment", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(20), server_default="uploaded", nullable=False, index=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("monthly_spend_cents", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "live_reference_exemplars",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "live_reference_id", sa.String(40),
            sa.ForeignKey("live_references.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("beat", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("live_reference_exemplars")
    op.drop_table("live_references")
