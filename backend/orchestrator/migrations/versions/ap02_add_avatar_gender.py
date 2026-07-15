"""add_avatar_gender

Revision ID: ap02_avatar_gender
Revises: ap01_avatar_pipeline
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "ap02_avatar_gender"
down_revision: Union[str, None] = "ap01_avatar_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("avatars", sa.Column("gender", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("avatars", "gender")
