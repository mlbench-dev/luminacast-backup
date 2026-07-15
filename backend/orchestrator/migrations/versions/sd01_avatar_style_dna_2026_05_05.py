"""add avatars.style_dna JSONB column for Avatar Style DNA feature

Revision ID: sd01_avatar_style_dna
Revises: m26_05_05_unify
Create Date: 2026-05-05

Adds a single JSONB column on `avatars` that stores the full Style DNA
profile (pacing, b-roll ratio, caption preset, hook pattern, signature
phrases, voice clone id, etc.) extracted from 1-3 reference creator
videos. Read by `engine/cast_generator.py` at production-plan time.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "sd01_avatar_style_dna"
down_revision: Union[str, Sequence[str], None] = "m26_05_05_unify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "avatars",
        sa.Column("style_dna", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("avatars", "style_dna")
