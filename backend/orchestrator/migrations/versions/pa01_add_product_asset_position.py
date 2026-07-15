"""add position column to product_assets

Revision ID: pa01_product_asset_position
Revises: e6f7a8b9c0d1
Create Date: 2026-05-04 00:00:00.000000

Gallery/carousel display ordering. position=0 is the cover; subsequent
assets imported from a product URL are written at position=1..N. Existing
rows are backfilled to 0; created_at is the secondary sort key in the
list endpoint, so legacy rows preserve their effective ordering.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = "pa01_product_asset_position"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_assets",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("product_assets", "position")
