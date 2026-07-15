"""add format_family, parent_cast_id, target_platforms, audio_stale_since to casts

Revision ID: p24_cast_format_family
Revises: m24_merge_all_heads
Create Date: 2026-04-15

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "p24_cast_format_family"
down_revision: Union[str, None] = "m24_merge_all_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "casts",
        sa.Column("format_family", sa.String(20), nullable=False, server_default="vertical"),
    )
    op.add_column(
        "casts",
        sa.Column(
            "parent_cast_id",
            sa.String(),
            sa.ForeignKey("casts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "casts",
        sa.Column("target_platforms", sa.JSON(), nullable=True, server_default="[]"),
    )
    op.add_column(
        "casts",
        sa.Column("audio_stale_since", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_casts_format_family", "casts", ["format_family"])
    op.create_index("ix_casts_parent_cast_id", "casts", ["parent_cast_id"])

    # Backfill format_family from output_format
    op.execute(
        "UPDATE casts SET format_family = 'horizontal' WHERE output_format = '16:9'"
    )
    op.execute(
        "UPDATE casts SET format_family = 'vertical' WHERE output_format != '16:9' OR output_format IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_casts_parent_cast_id", table_name="casts")
    op.drop_index("ix_casts_format_family", table_name="casts")
    op.drop_column("casts", "audio_stale_since")
    op.drop_column("casts", "target_platforms")
    op.drop_column("casts", "parent_cast_id")
    op.drop_column("casts", "format_family")
