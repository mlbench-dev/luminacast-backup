"""merge gauntlet deleted_at + avatar pipeline

Revision ID: 0edd3b81facd
Revises: ap01_avatar_pipeline, bb1c2d3delet4
Create Date: 2026-04-12

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0edd3b81facd'
down_revision: Union[str, None] = ('ap01_avatar_pipeline', 'bb1c2d3delet4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
