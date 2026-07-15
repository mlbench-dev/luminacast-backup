"""Add caption_words and caption_segments columns to variants

Revision ID: aa1b2c3d4e5f
Revises: n5f6g7h8i9j0
Create Date: 2026-04-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "aa1b2c3d4e5f"
down_revision: Union[str, None] = "n5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("variants", sa.Column("caption_words", sa.JSON(), nullable=True))
    op.add_column("variants", sa.Column("caption_segments", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("variants", "caption_segments")
    op.drop_column("variants", "caption_words")
