"""add FACE_CANDIDATES_READY to avatarstatus enum

Revision ID: b3f1a2c4d5e6
Revises: 88c51d3c92fd
Create Date: 2026-03-30 10:35:00.000000

B-082: FACE_CANDIDATES_READY enum value was in Python model but missing
from PostgreSQL avatarstatus enum, causing InvalidTextRepresentationError
when the image pipeline tried to set avatar status.
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'b3f1a2c4d5e6'
down_revision: Union[str, None] = '88c51d3c92fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL ALTER TYPE ADD VALUE cannot run inside a transaction
    # so we need to use autocommit mode
    op.execute("ALTER TYPE avatarstatus ADD VALUE IF NOT EXISTS 'FACE_CANDIDATES_READY'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # This is a one-way migration — FACE_CANDIDATES_READY stays.
    pass
