"""add commission_source, commission_category, affiliate_tag to products

Revision ID: cm01_commission_fields
Revises: vr01_version_render_cols, ph56_ci_wt
Create Date: 2026-05-05

Adds the three new commission-related columns and backfills
commission_source for existing rows so the upcoming product library
sort/filter behaviours work for products imported before this PR.

Backfill rule:
  - tiktok_product_id IS NOT NULL → tiktok_affiliate
  - else if product_url ILIKE '%amazon%' → amazon_associates
  - else                                 → manual
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cm01_commission_fields"
down_revision: Union[str, Sequence[str], None] = (
    "vr01_version_render_cols",
    "ph56_ci_wt",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("commission_source", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("commission_category", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("affiliate_tag", sa.String(length=100), nullable=True),
    )

    # Backfill commission_source for existing rows.
    op.execute(
        """
        UPDATE products
        SET commission_source = CASE
            WHEN tiktok_product_id IS NOT NULL THEN 'tiktok_affiliate'
            WHEN product_url ILIKE '%amazon%' THEN 'amazon_associates'
            ELSE 'manual'
        END
        WHERE commission_source IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("products", "affiliate_tag")
    op.drop_column("products", "commission_category")
    op.drop_column("products", "commission_source")
