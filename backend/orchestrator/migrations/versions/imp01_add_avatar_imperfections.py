"""add imperfections to avatars (Make It Real chips)

Revision ID: imp01_add_avatar_imperfections
Revises: prof01_add_user_avatar
Create Date: 2026-08-19

The AI Avatar setup step lets a user pick "Make It Real" chips (e.g.
freckles, phone selfie, tired after work) alongside a style preset, but
there was never a column to persist them — the save-setup endpoint
accepted them and silently dropped them, so they never survived
navigating away from the Setup step and back. Guarded with a
column-existence check, matching the repo's convention for ALTERs of
long-lived tables (see prof01_add_user_avatar.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "imp01_add_avatar_imperfections"
down_revision: Union[str, None] = "prof01_add_user_avatar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("avatars")}
    if "imperfections" not in cols:
        op.add_column("avatars", sa.Column("imperfections", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("avatars")}
    if "imperfections" in cols:
        op.drop_column("avatars", "imperfections")
