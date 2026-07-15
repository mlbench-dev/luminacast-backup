"""add regeneration_count to avatars

Revision ID: c4a7e8f91b23
Revises: b3f1a2c4d5e6
Create Date: 2026-03-31 12:00:00.000000

B-099: Track how many times an avatar has been regenerated (2 free, then block).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c4a7e8f91b23'
down_revision: Union[str, None] = 'b3f1a2c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('avatars', sa.Column('regeneration_count', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('avatars', 'regeneration_count')
