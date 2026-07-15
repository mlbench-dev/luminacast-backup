"""add layout_template_id to casts (Step 3 — template attach)

Revision ID: lt03_cast_layout_template_id
Revises: uvt01_user_video_thumbnail
Create Date: 2026-06-14

Adds a cast-level `casts.layout_template_id` (VARCHAR, nullable) FK to
`layout_templates.id` with ON DELETE SET NULL, indexed. This records which
layout-template preset a generated cast resolved to (Step 2 selector). It is a
read-only field for now — the composer/timeline_builder/renderer do NOT read it
yet (that is Step 4). Nullable so existing casts and any cast whose selection
fails keep working.

Parented on `uvt01_user_video_thumbnail`, the real production head for the
casts table (verified clean: 68 ancestors, zero phantom revisions, and both the
layout_templates table migration `d5e6f7a8b9c0` and the casts expansion
`e6f7a8b9c0d1` are in its ancestry). The four known phantom heads
(`a8b9c0d1e2f3`, `b1c2d3e4f5a6`, `c2d3e4f5a6b7`, `m26_05_05_unify_post_ual01`)
are deliberately NOT parented on.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "lt03_cast_layout_template_id"
down_revision: Union[str, Sequence[str], None] = "uvt01_user_video_thumbnail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "casts",
        sa.Column("layout_template_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_casts_layout_template_id",
        "casts",
        ["layout_template_id"],
    )
    op.create_foreign_key(
        "fk_casts_layout_template_id",
        "casts",
        "layout_templates",
        ["layout_template_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_casts_layout_template_id", "casts", type_="foreignkey")
    op.drop_index("ix_casts_layout_template_id", table_name="casts")
    op.drop_column("casts", "layout_template_id")
