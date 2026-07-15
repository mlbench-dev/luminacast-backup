"""add production_level to casts (and unify open heads) — 2026-05-05

Revision ID: pl01_production_level
Revises: cl01_clips, up01_user_photos
Create Date: 2026-05-05

Adds `casts.production_level` (VARCHAR(20), default 'standard') to back the
new Production Level selector in SetupPhase (replaces the AI-Plan chip
strip). Three values: quick / standard / premium. Used at outline-time to
constrain block-category mix.

Also unifies the two open heads `cl01_clips` and `up01_user_photos` so
alembic can upgrade cleanly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "pl01_production_level"
down_revision: Union[str, Sequence[str], None] = ("cl01_clips", "up01_user_photos")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "casts",
        sa.Column(
            "production_level",
            sa.String(length=20),
            nullable=False,
            server_default="standard",
        ),
    )


def downgrade() -> None:
    op.drop_column("casts", "production_level")
