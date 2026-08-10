"""add body motion frame prompts + widen avatar_look look_type

Revision ID: cb66416bce46
Revises: z7a8b9c0d1e2
Create Date: 2026-05-08

Adds:
  - blocks.body_motion_start_prompt, blocks.body_motion_end_prompt — the
    Opus script-writer fills these with frame descriptions (camera angle,
    pose, expression, scene) which seed AI-generated start/end frames for
    body-motion blocks.
  - Widens avatar_looks.look_type from VARCHAR(20) to VARCHAR(80) so the
    per-block frame look_type (`body_motion_block_<block_id>_<kind>`)
    fits without truncation.

"""
from alembic import op
import sqlalchemy as sa

revision = "cb66416bce46"
down_revision = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE blocks
        ADD COLUMN IF NOT EXISTS body_motion_start_prompt TEXT,
        ADD COLUMN IF NOT EXISTS body_motion_end_prompt TEXT;
        """
    )
    # Widen look_type so the per-block keys we mint
    # (body_motion_block_<block_id>_<kind>) — up to ~40 chars including
    # the blk_ prefix and 12 hex chars — fit. Old rows keep their values.
    op.execute(
        "ALTER TABLE avatar_looks ALTER COLUMN look_type TYPE VARCHAR(80);"
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE blocks
        DROP COLUMN IF EXISTS body_motion_end_prompt,
        DROP COLUMN IF EXISTS body_motion_start_prompt;
        """
    )
    op.execute(
        "ALTER TABLE avatar_looks ALTER COLUMN look_type TYPE VARCHAR(20);"
    )
