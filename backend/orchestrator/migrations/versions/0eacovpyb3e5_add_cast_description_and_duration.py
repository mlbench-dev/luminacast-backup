"""add cast description, duration_target_seconds, platform_target

Revision ID: 0eacovpyb3e5
Revises: fv01_final_video_url
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0eacovpyb3e5"
down_revision: Union[str, None] = "fv01_final_video_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("casts", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("casts", sa.Column("duration_target_seconds", sa.Integer(), nullable=True))
    op.add_column("casts", sa.Column("platform_target", sa.String(20), nullable=True, server_default="tiktok"))


def downgrade() -> None:
    op.drop_column("casts", "platform_target")
    op.drop_column("casts", "duration_target_seconds")
    op.drop_column("casts", "description")
