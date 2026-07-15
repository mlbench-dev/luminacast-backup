"""add final_video_url to casts

Revision ID: fv01_final_video_url
Revises: 0edd3b81facd
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "fv01_final_video_url"
down_revision: Union[str, None] = "0edd3b81facd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("casts", sa.Column("final_video_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("casts", "final_video_url")
