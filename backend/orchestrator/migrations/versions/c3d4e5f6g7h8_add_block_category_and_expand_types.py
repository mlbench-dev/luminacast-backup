"""add block category and expand block types

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-04-11 12:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6g7h8"
down_revision: Union[str, None] = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new block type values to the existing enum
    # Note: PostgreSQL requires ALTER TYPE ... ADD VALUE for enum extensions
    for val in [
        "HOOK", "PRODUCT_DEMO", "FEATURE_SHOWCASE", "COMPARISON",
        "URGENCY", "EDUCATIONAL", "STORY", "TRANSITION",
        # Normalize existing lowercase values
        "TESTIMONIAL", "QA",
    ]:
        op.execute(f"ALTER TYPE blocktype ADD VALUE IF NOT EXISTS '{val}'")

    # Add category column
    op.add_column(
        "blocks",
        sa.Column("category", sa.String(30), server_default="avatar_speaking", nullable=False),
    )

    # Add cast_type to casts for Live vs Recorded toggle
    op.add_column(
        "casts",
        sa.Column("cast_type", sa.String(20), server_default="recorded", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("casts", "cast_type")
    op.drop_column("blocks", "category")
    # Note: PostgreSQL does not support removing enum values
