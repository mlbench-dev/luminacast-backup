"""add script_direction to casts

Revision ID: z7a8b9c0d1e2
Revises: 342ae0fe54c6
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "z7a8b9c0d1e2"
down_revision: Union[str, None] = "342ae0fe54c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("casts", sa.Column("script_direction", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("casts", "script_direction")
