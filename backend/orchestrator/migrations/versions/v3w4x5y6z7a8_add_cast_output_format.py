"""add output_format to casts

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = "v3w4x5y6z7a8"
down_revision = "u2v3w4x5y6z7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='casts' AND column_name='output_format'
            ) THEN
                ALTER TABLE casts ADD COLUMN output_format VARCHAR(10) NOT NULL DEFAULT '9:16';
            END IF;
        END$$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='casts' AND column_name='output_format'
            ) THEN
                ALTER TABLE casts DROP COLUMN output_format;
            END IF;
        END$$;
    """)
