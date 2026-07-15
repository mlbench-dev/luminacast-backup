"""add avatar_angle to blocks

Revision ID: d3d540cda4fa
Revises: af379dd4a036
Create Date: 2026-04-14 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d3d540cda4fa"
down_revision: Union[str, None] = "af379dd4a036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "blocks",
        sa.Column("avatar_angle", sa.String(32), server_default="front", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("blocks", "avatar_angle")
