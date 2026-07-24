"""merge avatar action fields

Revision ID: b1c2d3e4f5a6
Revises: 
Create Date: 2026-07-23

"""
from alembic import op


revision = "b1c2d3e4f5a6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE blocks
        SET category = 'avatar_action'
        WHERE category IN ('avatar_motion', 'avatar_acting')
        """
    )
    op.execute("ALTER TABLE blocks ADD COLUMN IF NOT EXISTS action_start_prompt TEXT")
    op.execute("ALTER TABLE blocks ADD COLUMN IF NOT EXISTS action_end_prompt TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE blocks DROP COLUMN IF EXISTS action_end_prompt")
    op.execute("ALTER TABLE blocks DROP COLUMN IF EXISTS action_start_prompt")
