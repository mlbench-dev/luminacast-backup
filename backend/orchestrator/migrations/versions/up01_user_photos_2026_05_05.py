"""create user_photo_assets table for user-uploaded photos

Revision ID: up01_user_photos
Revises: sd01_avatar_style_dna
Create Date: 2026-05-05

Adds the `user_photo_assets` table to back the new POST /api/user-photos
upload endpoint used by the cast-builder editor's media panel. Mirrors
the shape of `user_video_assets`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "up01_user_photos"
down_revision: Union[str, Sequence[str], None] = "sd01_avatar_style_dna"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_photo_assets",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("user_id", sa.String(length=40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("r2_key", sa.String(length=500), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("original_filename", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_user_photo_assets_user_id",
        "user_photo_assets",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_photo_assets_user_id", table_name="user_photo_assets")
    op.drop_table("user_photo_assets")
