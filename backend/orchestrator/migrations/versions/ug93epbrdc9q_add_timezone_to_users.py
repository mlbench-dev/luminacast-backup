"""add timezone to users

Revision ID: ug93epbrdc9q
Revises: z7a8b9c0d1e2
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ug93epbrdc9q"
down_revision: Union[str, None] = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("timezone", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "timezone")
