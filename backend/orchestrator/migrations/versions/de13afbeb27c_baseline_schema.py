"""baseline_schema

Revision ID: de13afbeb27c
Revises: 
Create Date: 2026-03-29 19:26:11.645363
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'de13afbeb27c'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
