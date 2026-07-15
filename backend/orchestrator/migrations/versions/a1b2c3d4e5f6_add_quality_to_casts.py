"""Add quality column to casts table

Revision ID: a1b2c3d4e5f6
Revises: e6f7a8b9c0d1
Create Date: 2026-04-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    cast_quality = sa.Enum('simple', 'hd', 'hd_plus', name='castquality')
    cast_quality.create(op.get_bind(), checkfirst=True)
    op.add_column('casts', sa.Column('quality', sa.Enum('simple', 'hd', 'hd_plus', name='castquality'), server_default='simple', nullable=True))

def downgrade() -> None:
    op.drop_column('casts', 'quality')
    sa.Enum(name='castquality').drop(op.get_bind(), checkfirst=True)
