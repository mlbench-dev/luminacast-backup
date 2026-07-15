"""merge caption and script_direction heads

Revision ID: ef0aa3aa980d
Revises: aa1b2c3d4e5f
Create Date: 2026-04-11 06:59:41.842035

NOTE 2026-05-05: original `down_revision` was `('aa1b2c3d4e5f', 'z7a8b9c0d1e2')`,
but `z7a8b9c0d1e2` is actually a DESCENDANT of this migration (not an ancestor)
via the chain ef0 → aa1b2c3style5 → b2c3d4e5f6g7 → ... → z7a8b9c0d1e2. That
formed a cycle in the revision graph and crashed `alembic heads`/`upgrade`.
Removed the bogus z7 reference. This migration was never applied in production
(prod alembic_version was at p24_cast_format_family at fix time), so no DB-state
correction is needed.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'ef0aa3aa980d'
down_revision: Union[str, None] = 'aa1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
