"""auto-clips + child casts on Cast — 2026-05-05

Revision ID: cl01_clips
Revises: m26_05_05_unify
Create Date: 2026-05-05

CHANGE 5/6 of the Publish/Channels/Style DNA/Clips spec — cast_generator now
suggests 2-4 standalone short clips per cast, and approving one creates a
child Cast that renders via FFmpeg trim against the parent's already-composed
mp4 (no GPU work).

Columns added on `casts`:
- suggested_clips JSONB: LLM output [{name, block_ids, duration_seconds, best_platforms, why}]
- approved_clips JSONB: subset the user kept
- clip_parent_cast_id (FK casts.id, ON DELETE CASCADE): set on child casts —
  when the parent is deleted, all clip-children go with it (no orphan clips).
  We deliberately do NOT reuse the existing `parent_cast_id` column (which
  has SET NULL semantics for cross-format duplication) — different lifecycle.
- clip_block_ids JSONB: list of parent block IDs the clip stitches together.
  Stored as IDs (not positions) so reordering / deleting parent blocks
  doesn't silently re-point an approved clip at the wrong segment.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "cl01_clips"
down_revision: Union[str, Sequence[str], None] = "m26_05_05_unify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "casts",
        sa.Column("suggested_clips", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "casts",
        sa.Column("approved_clips", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "casts",
        sa.Column("clip_parent_cast_id", sa.String(), nullable=True),
    )
    op.add_column(
        "casts",
        sa.Column("clip_block_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_casts_clip_parent_cast_id",
        "casts",
        ["clip_parent_cast_id"],
    )
    op.create_foreign_key(
        "fk_casts_clip_parent_cast_id",
        "casts",
        "casts",
        ["clip_parent_cast_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_casts_clip_parent_cast_id", "casts", type_="foreignkey")
    op.drop_index("ix_casts_clip_parent_cast_id", table_name="casts")
    op.drop_column("casts", "clip_block_ids")
    op.drop_column("casts", "clip_parent_cast_id")
    op.drop_column("casts", "approved_clips")
    op.drop_column("casts", "suggested_clips")
