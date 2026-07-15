"""Add background_id to blocks, motion_prompt to variants

Revision ID: l3d4e5f6g7h8
Revises: k2c3d4e5f6g7
Create Date: 2026-04-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "l3d4e5f6g7h8"
down_revision: Union[str, None] = "k2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("background_id", sa.String(), nullable=True))
    op.create_index("ix_blocks_background_id", "blocks", ["background_id"])
    op.create_foreign_key("fk_blocks_background_id", "blocks", "avatar_backgrounds", ["background_id"], ["id"])
    op.add_column("variants", sa.Column("motion_prompt", sa.Text(), server_default=""))


def downgrade() -> None:
    op.drop_column("variants", "motion_prompt")
    op.drop_constraint("fk_blocks_background_id", "blocks", type_="foreignkey")
    op.drop_index("ix_blocks_background_id", table_name="blocks")
    op.drop_column("blocks", "background_id")
