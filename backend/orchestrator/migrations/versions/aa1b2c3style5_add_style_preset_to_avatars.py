"""add style_preset to avatars

Revision ID: a1b2c3d4e5f6
Revises: ef0aa3aa980d
Create Date: 2026-04-11 10:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "aa1b2c3style5"
down_revision: Union[str, None] = "ef0aa3aa980d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("avatars", sa.Column("style_preset", sa.String(50), nullable=True, server_default="studio"))


def downgrade() -> None:
    op.drop_column("avatars", "style_preset")
