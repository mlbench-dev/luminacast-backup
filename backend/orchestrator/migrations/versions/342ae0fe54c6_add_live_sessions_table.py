"""add_live_sessions_table

Revision ID: 342ae0fe54c6
Revises: y6z7a8b9c0d1
Create Date: 2026-04-10 12:33:10.226691
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "342ae0fe54c6"
down_revision: Union[str, None] = "y6z7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("avatar_id", sa.String(40), sa.ForeignKey("avatars.id"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False, index=True),
        sa.Column("product_queue", sa.JSON(), nullable=True),
        sa.Column("voice_style_notes", sa.Text(), nullable=True),
        sa.Column("max_duration_minutes", sa.Integer(), server_default="60"),
        sa.Column("output_format", sa.String(10), server_default="9:16"),
        sa.Column("current_product_index", sa.Integer(), server_default="0"),
        sa.Column("current_paragraph", sa.Text(), nullable=True),
        sa.Column("stream_key", sa.String(100), nullable=True),
        sa.Column("hls_url", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("total_paragraphs_generated", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("live_sessions")
