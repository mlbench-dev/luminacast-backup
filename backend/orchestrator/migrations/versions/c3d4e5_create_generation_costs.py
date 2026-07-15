"""create generation_costs table

Revision ID: af379dd4a036
Revises: aad105faa71f
Create Date: 2026-04-14 14:46:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "af379dd4a036"
down_revision: Union[str, None] = "aad105faa71f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_costs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), index=True, nullable=True),
        sa.Column("avatar_id", sa.String(40), sa.ForeignKey("avatars.id"), index=True, nullable=True),
        sa.Column("cast_id", sa.String(40), sa.ForeignKey("casts.id"), index=True, nullable=True),
        sa.Column("block_id", sa.String(40), nullable=True, index=True),
        sa.Column("engine", sa.String(50), nullable=False),
        sa.Column("operation", sa.String(50), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("quantity", sa.Integer, default=1),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("generation_costs")
