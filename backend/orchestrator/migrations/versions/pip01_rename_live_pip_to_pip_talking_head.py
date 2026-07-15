"""rename block category live_pip to pip_talking_head

Revision ID: pip01_rename_pip_th
Revises: d3d540cda4fa
Create Date: 2026-05-04 00:00:00.000000

The `blocks.category` column is a plain VARCHAR(30) (not a Postgres
ENUM type), so the migration is a single data UPDATE. No schema
changes are needed — existing code paths continue to read whatever
string is in the column.

The downgrade reverses the UPDATE so the migration is reversible.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "pip01_rename_pip_th"
down_revision: Union[str, None] = "d3d540cda4fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE blocks SET category = 'pip_talking_head' "
        "WHERE category = 'live_pip'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE blocks SET category = 'live_pip' "
        "WHERE category = 'pip_talking_head'"
    )
