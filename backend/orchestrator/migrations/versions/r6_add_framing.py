"""add framing to avatar_looks (Round-6 Bug B)

Revision ID: r6_add_framing
Revises: lref01_live_references
Create Date: 2026-06-17 12:30:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "r6_add_framing"
down_revision: Union[str, None] = "lref01_live_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "avatar_looks",
        sa.Column(
            "framing",
            sa.String(length=20),
            nullable=False,
            server_default="MEDIUM",
        ),
    )
    op.add_column(
        "blocks",
        sa.Column(
            "framing",
            sa.String(length=20),
            nullable=False,
            server_default="MEDIUM",
        ),
    )
    # Backfill existing rows to the default framing.
    op.execute("UPDATE avatar_looks SET framing = 'MEDIUM' WHERE framing IS NULL")
    op.execute("UPDATE blocks SET framing = 'MEDIUM' WHERE framing IS NULL")


def downgrade() -> None:
    op.drop_column("blocks", "framing")
    op.drop_column("avatar_looks", "framing")
