"""pending_social_connects table + unique index on social_channels.zernio_account_id

Revision ID: pconn01_social_connect
Revises: qual01_quality_render_usage
Create Date: 2026-08-21

Part of fixing the cross-user Zernio data leak: pending_social_connects
bridges an OAuth connect attempt back to the initiating user (Zernio's
redirect never tells us which account was connected, only platform+status),
and the partial unique index on social_channels.zernio_account_id makes it
impossible for the same external Zernio account to be claimed by more than
one Luminacast user's channel row, even if application logic has a future
bug. Column-existence/table-existence guarded per this repo's convention for
ALTERs of long-lived tables (see qual01_add_quality_to_render_usage.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "pconn01_social_connect"
down_revision: Union[str, None] = "qual01_quality_render_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "pending_social_connects" not in existing_tables:
        op.create_table(
            "pending_social_connects",
            sa.Column("id", sa.String(length=40), primary_key=True),
            sa.Column("user_id", sa.String(length=40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("platform", sa.String(length=20), nullable=False),
            sa.Column("before_zernio_account_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index(
            "ix_pending_social_connects_user_id", "pending_social_connects", ["user_id"]
        )
        op.create_index(
            "ix_pending_social_connects_user_platform",
            "pending_social_connects",
            ["user_id", "platform"],
        )

    if "social_channels" in existing_tables:
        existing_indexes = {ix["name"] for ix in inspector.get_indexes("social_channels")}
        if "ux_social_channels_zernio_account_id" not in existing_indexes:
            op.create_index(
                "ux_social_channels_zernio_account_id",
                "social_channels",
                ["zernio_account_id"],
                unique=True,
                postgresql_where=sa.text("zernio_account_id IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "social_channels" in existing_tables:
        existing_indexes = {ix["name"] for ix in inspector.get_indexes("social_channels")}
        if "ux_social_channels_zernio_account_id" in existing_indexes:
            op.drop_index("ux_social_channels_zernio_account_id", table_name="social_channels")

    if "pending_social_connects" in existing_tables:
        op.drop_table("pending_social_connects")
