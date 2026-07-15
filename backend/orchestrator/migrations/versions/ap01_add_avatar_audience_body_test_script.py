"""add_avatar_audience_body_test_script

Revision ID: ap01_avatar_pipeline
Revises: c3d4e5f6g7h8
Create Date: 2026-04-12 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "ap01_avatar_pipeline"
down_revision: Union[str, None] = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # avatars: new columns for audience, body description, locked test script, preview video
    op.add_column("avatars", sa.Column("target_audience", sa.JSON(), nullable=True))
    op.add_column("avatars", sa.Column("body_description", sa.Text(), nullable=True))
    op.add_column("avatars", sa.Column("locked_test_script", sa.Text(), nullable=True))
    op.add_column("avatars", sa.Column("preview_video_key", sa.String(), nullable=True))

    # casts: target audience override
    op.add_column("casts", sa.Column("target_audience_override", sa.JSON(), nullable=True))

    # body_shot_sets table
    op.create_table(
        "body_shot_sets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("avatar_id", sa.String(), sa.ForeignKey("avatars.id"), nullable=False, index=True),
        sa.Column("front_shot_key", sa.String(), nullable=True),
        sa.Column("angles", sa.JSON(), nullable=True),
        sa.Column("description_used", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("body_shot_sets")
    op.drop_column("casts", "target_audience_override")
    op.drop_column("avatars", "preview_video_key")
    op.drop_column("avatars", "locked_test_script")
    op.drop_column("avatars", "body_description")
    op.drop_column("avatars", "target_audience")
