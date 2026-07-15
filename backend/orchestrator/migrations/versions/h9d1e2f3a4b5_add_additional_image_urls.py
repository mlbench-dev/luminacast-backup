"""Add additional_image_urls to trending_products

Revision ID: h9d1e2f3a4b5
Revises: h9c0a1d2e3f4
Create Date: 2026-04-04

Store all product image URLs from Apify (imageUrls array), not just cover.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'h9d1e2f3a4b5'
down_revision: Union[str, None] = 'h9c0a1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trending_products", sa.Column("additional_image_urls", sa.JSON(), server_default="[]"))


def downgrade() -> None:
    op.drop_column("trending_products", "additional_image_urls")
