"""add is_original to avatar_looks

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-11 11:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6g7"
down_revision: Union[str, None] = "aa1b2c3style5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("avatar_looks", sa.Column("is_original", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("avatar_looks", "is_original")
