"""add body motion fields to blocks

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-04-10

"""
from alembic import op
import sqlalchemy as sa

revision = "y6z7a8b9c0d1"
down_revision = "x5y6z7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE blocks
        ADD COLUMN IF NOT EXISTS body_motion_start_look_id VARCHAR(40) REFERENCES avatar_looks(id),
        ADD COLUMN IF NOT EXISTS body_motion_end_look_id VARCHAR(40) REFERENCES avatar_looks(id),
        ADD COLUMN IF NOT EXISTS body_motion_prompt TEXT;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_blocks_body_motion_start_look_id ON blocks (body_motion_start_look_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_blocks_body_motion_end_look_id ON blocks (body_motion_end_look_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_blocks_body_motion_end_look_id;")
    op.execute("DROP INDEX IF EXISTS ix_blocks_body_motion_start_look_id;")
    op.execute("""
        ALTER TABLE blocks
        DROP COLUMN IF EXISTS body_motion_prompt,
        DROP COLUMN IF EXISTS body_motion_end_look_id,
        DROP COLUMN IF EXISTS body_motion_start_look_id;
    """)
