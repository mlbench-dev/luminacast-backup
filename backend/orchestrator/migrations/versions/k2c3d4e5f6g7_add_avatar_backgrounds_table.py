"""Add avatar_backgrounds table

Revision ID: k2c3d4e5f6g7
Revises: j1b2c3d4e5f6
Create Date: 2026-04-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "k2c3d4e5f6g7"
down_revision: Union[str, None] = "j1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "avatar_backgrounds",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("avatar_id", sa.String(), sa.ForeignKey("avatars.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default="Untitled Background"),
        sa.Column("r2_key", sa.String(), nullable=False),
        sa.Column("thumbnail_r2_key", sa.String(), server_default=""),
        sa.Column("source", sa.String(), server_default="upload"),
        sa.Column("generation_prompt", sa.String(), server_default=""),
        sa.Column("width", sa.Integer(), server_default="0"),
        sa.Column("height", sa.Integer(), server_default="0"),
        sa.Column("file_size_bytes", sa.Integer(), server_default="0"),
        sa.Column("position", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_avatar_backgrounds_avatar_id", "avatar_backgrounds", ["avatar_id"])
    op.create_index("ix_avatar_backgrounds_user_id", "avatar_backgrounds", ["user_id"])
    op.create_index("ix_avatar_backgrounds_source", "avatar_backgrounds", ["source"])


def downgrade() -> None:
    op.drop_index("ix_avatar_backgrounds_source", table_name="avatar_backgrounds")
    op.drop_index("ix_avatar_backgrounds_user_id", table_name="avatar_backgrounds")
    op.drop_index("ix_avatar_backgrounds_avatar_id", table_name="avatar_backgrounds")
    op.drop_table("avatar_backgrounds")
