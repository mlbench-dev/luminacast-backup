"""create cast_renders table

Revision ID: f1a2b3c4d5e6
Revises: 067cae3e25c1
Create Date: 2026-04-14 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "067cae3e25c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cast_renders",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("cast_id", sa.String(40), sa.ForeignKey("casts.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued", index=True),
        sa.Column("timeline_snapshot", sa.JSON, nullable=False),
        sa.Column("output_video_r2_key", sa.String(512), nullable=True),
        sa.Column("baking_chunks_total", sa.Integer, nullable=True),
        sa.Column("baking_chunks_completed", sa.Integer, server_default="0"),
        sa.Column("render_attempt", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("cast_renders")
