"""add variant is_active and created_at columns

Revision ID: a8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-04-15 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "z7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_active column with server default
    op.add_column(
        "variants",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_variants_is_active", "variants", ["is_active"])

    # Add created_at column with server default
    op.add_column(
        "variants",
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )

    # Backfill: for each block, mark the most-recently-updated variant as active,
    # all others inactive. Since created_at was just added (all NULL), use updated_at
    # or id as a tiebreaker.
    op.execute("""
        WITH ranked AS (
            SELECT id, block_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY block_id
                       ORDER BY COALESCE(updated_at, '1970-01-01'::timestamp) DESC, id DESC
                   ) AS rn
            FROM variants
        )
        UPDATE variants
        SET is_active = (ranked.rn = 1)
        FROM ranked
        WHERE variants.id = ranked.id
    """)


def downgrade() -> None:
    op.drop_index("ix_variants_is_active", table_name="variants")
    op.drop_column("variants", "is_active")
    op.drop_column("variants", "created_at")
