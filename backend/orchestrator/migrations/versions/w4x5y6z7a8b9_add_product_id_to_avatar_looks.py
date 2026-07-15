"""add product_id to avatar_looks

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = "w4x5y6z7a8b9"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'avatar_looks' AND column_name = 'product_id'
            ) THEN
                ALTER TABLE avatar_looks ADD COLUMN product_id VARCHAR(40);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE avatar_looks DROP COLUMN IF EXISTS product_id")
