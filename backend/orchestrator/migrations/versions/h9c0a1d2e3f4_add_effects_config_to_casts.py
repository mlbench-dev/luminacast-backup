"""Add effects_config JSON column to casts table

Revision ID: h9c0a1d2e3f4
Revises: g8a9b0c1d2e3
Create Date: 2026-04-03

Stores per-cast effects configuration: product overlay position/size,
floating reactions, confetti, lower third banner, etc.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'h9c0a1d2e3f4'
down_revision: Union[str, None] = 'g8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('casts', sa.Column('effects_config', sa.JSON(), nullable=True))

def downgrade() -> None:
    op.drop_column('casts', 'effects_config')
