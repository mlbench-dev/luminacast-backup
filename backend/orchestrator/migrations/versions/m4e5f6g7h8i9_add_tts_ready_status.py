"""Add TTS_READY to cast status enum

Revision ID: m4e5f6g7h8i9
Revises: l3d4e5f6g7h8
Create Date: 2026-04-05
"""
from typing import Sequence, Union
from alembic import op

revision: str = "m4e5f6g7h8i9"
down_revision: Union[str, None] = "l3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new enum values to caststatus
    op.execute("ALTER TYPE caststatus ADD VALUE IF NOT EXISTS 'generating_tts'")
    op.execute("ALTER TYPE caststatus ADD VALUE IF NOT EXISTS 'tts_ready'")
    op.execute("ALTER TYPE caststatus ADD VALUE IF NOT EXISTS 'generating_videos'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values easily
    # Just leave them — they're harmless
    pass
