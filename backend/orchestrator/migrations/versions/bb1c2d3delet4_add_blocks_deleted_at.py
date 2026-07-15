"""add deleted_at to blocks for soft delete

Revision ID: a1b2c3d4e5f6
Revises: z7a8b9c0d1e2
Create Date: 2026-04-12 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "bb1c2d3delet4"
down_revision: Union[str, None] = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_blocks_deleted_at", "blocks", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_blocks_deleted_at", table_name="blocks")
    op.drop_column("blocks", "deleted_at")
