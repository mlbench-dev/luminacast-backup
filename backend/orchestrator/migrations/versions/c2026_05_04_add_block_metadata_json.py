"""add metadata json column to blocks (product carousel + future per-block flags)

Revision ID: c2026_05_04_blkmeta
Revises: ph56_ci_wt
Create Date: 2026-05-04

Why: the product carousel feature stores its toggle + speed + transition +
ordered asset_ids on the block. Rather than bolt on three columns for one
feature, give blocks a generic JSONB `metadata` bag that future per-block
ad-hoc flags can also use without another migration each time.

The column is nullable; legacy rows read as NULL and are treated as {} by
the application layer.
"""
from alembic import op


revision = "c2026_05_04_blkmeta"
down_revision = "ph56_ci_wt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE blocks
        ADD COLUMN IF NOT EXISTS metadata JSONB;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE blocks
        DROP COLUMN IF EXISTS metadata;
        """
    )
