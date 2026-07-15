"""add look_type, pose_angle to avatar_looks and pip_engine to blocks

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = "u2v3w4x5y6z7"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'avatar_looks' AND column_name = 'look_type'
            ) THEN
                ALTER TABLE avatar_looks ADD COLUMN look_type VARCHAR(20) NOT NULL DEFAULT 'background';
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'avatar_looks' AND column_name = 'pose_angle'
            ) THEN
                ALTER TABLE avatar_looks ADD COLUMN pose_angle VARCHAR(20);
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'blocks' AND column_name = 'pip_engine'
            ) THEN
                ALTER TABLE blocks ADD COLUMN pip_engine VARCHAR(30) NOT NULL DEFAULT 'infinitetalk_rendered';
            END IF;
        END
        $$;
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_avatar_looks_look_type ON avatar_looks (look_type)")
    op.execute("UPDATE avatar_looks SET look_type = 'background' WHERE look_type IS NULL")
    op.execute("UPDATE blocks SET pip_engine = 'infinitetalk_rendered' WHERE pip_engine IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE avatar_looks DROP COLUMN IF EXISTS look_type")
    op.execute("ALTER TABLE avatar_looks DROP COLUMN IF EXISTS pose_angle")
    op.execute("ALTER TABLE blocks DROP COLUMN IF EXISTS pip_engine")
    op.execute("DROP INDEX IF EXISTS ix_avatar_looks_look_type")
