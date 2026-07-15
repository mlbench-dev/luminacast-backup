"""Add is_active column to blocks table

Revision ID: j1b2c3d4e5f6
Revises: i0a1b2c3d4e5
Create Date: 2026-04-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "j1b2c3d4e5f6"
down_revision: Union[str, None] = "i0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.create_index("ix_blocks_is_active", "blocks", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_blocks_is_active", table_name="blocks")
    op.drop_column("blocks", "is_active")
