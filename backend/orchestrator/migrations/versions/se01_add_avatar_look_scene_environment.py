"""add scene environment fields to avatar_looks

Revision ID: se01_avatar_look_scene_env
Revises: r6_add_framing
Create Date: 2026-07-23

"""
from alembic import op


revision = "se01_avatar_look_scene_env"
down_revision = "r6_add_framing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE avatar_looks
        ADD COLUMN IF NOT EXISTS environment VARCHAR(20) NOT NULL DEFAULT 'studio'
        """
    )
    op.execute(
        """
        ALTER TABLE avatar_looks
        ADD COLUMN IF NOT EXISTS mic_visible BOOLEAN NOT NULL DEFAULT FALSE
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE avatar_looks DROP COLUMN IF EXISTS mic_visible")
    op.execute("ALTER TABLE avatar_looks DROP COLUMN IF EXISTS environment")

