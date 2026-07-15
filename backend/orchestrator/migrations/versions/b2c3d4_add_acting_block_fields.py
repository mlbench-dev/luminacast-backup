"""add block_type and acting fields to blocks

Revision ID: aad105faa71f
Revises: 067cae3e25c1
Create Date: 2026-04-14 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "aad105faa71f"
down_revision: Union[str, None] = "067cae3e25c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "blocks",
        sa.Column("block_type", sa.String(20), server_default="speaking", nullable=False),
    )
    op.add_column(
        "blocks",
        sa.Column("acting_prompt", sa.Text, nullable=True),
    )
    op.add_column(
        "blocks",
        sa.Column("acting_first_frame_angle", sa.String(32), nullable=True),
    )
    op.add_column(
        "blocks",
        sa.Column("acting_last_frame_angle", sa.String(32), nullable=True),
    )
    op.add_column(
        "blocks",
        sa.Column("acting_video_r2_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "blocks",
        sa.Column("acting_video_duration_seconds", sa.Float, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("blocks", "acting_video_duration_seconds")
    op.drop_column("blocks", "acting_video_r2_key")
    op.drop_column("blocks", "acting_last_frame_angle")
    op.drop_column("blocks", "acting_first_frame_angle")
    op.drop_column("blocks", "acting_prompt")
    op.drop_column("blocks", "block_type")
