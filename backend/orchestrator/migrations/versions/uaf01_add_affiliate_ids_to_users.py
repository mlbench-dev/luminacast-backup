"""add tiktok_affiliate_id and amazon_associate_tag to users

Revision ID: uaf01_user_affiliates
Revises: ph56_ci_wt
Create Date: 2026-05-05

Adds two nullable affiliate-id columns so creators can connect their
TikTok Shop affiliate ID and Amazon Associates tracking tag from
Settings -> My Channels.

Both columns are NULLABLE on purpose -- a missing value means the
platform is not connected, and the product page surfaces a CTA that
points the user at /settings/channels#affiliates.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "uaf01_user_affiliates"
down_revision: Union[str, Sequence[str], None] = "ph56_ci_wt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tiktok_affiliate_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("amazon_associate_tag", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "amazon_associate_tag")
    op.drop_column("users", "tiktok_affiliate_id")
