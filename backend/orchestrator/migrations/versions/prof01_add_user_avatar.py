"""add avatar_r2_key to users (profile picture)

Revision ID: prof01_add_user_avatar
Revises: bil01_add_billing_tables
Create Date: 2026-08-12

Adds the profile-picture column for the account settings page. Guarded
with a column-existence check, matching the repo's convention for ALTERs
of long-lived tables (see m28_teams_and_approval_status.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "prof01_add_user_avatar"
down_revision: Union[str, None] = "bil01_add_billing_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "avatar_r2_key" not in cols:
        op.add_column("users", sa.Column("avatar_r2_key", sa.String(500), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "avatar_r2_key" in cols:
        op.drop_column("users", "avatar_r2_key")
