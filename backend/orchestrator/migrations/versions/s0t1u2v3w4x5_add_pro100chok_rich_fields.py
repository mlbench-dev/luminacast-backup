"""add pro100chok rich product fields

Revision ID: s0t1u2v3w4x5
Revises: r9j0k1l2m3n4
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "s0t1u2v3w4x5"
down_revision = "r9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TrendingProduct: rich detail fields populated by pro100chok product mode
    # NOTE: video_urls already exists from migration p7h8 — skip
    op.add_column("trending_products", sa.Column("variants", postgresql.JSON(), nullable=True))
    op.add_column("trending_products", sa.Column("specifications", postgresql.JSON(), nullable=True))
    op.add_column("trending_products", sa.Column("selling_points", postgresql.JSON(), nullable=True))
    op.add_column("trending_products", sa.Column("full_description", sa.Text(), nullable=True))
    op.add_column("trending_products", sa.Column("sold_last_30_days", sa.Integer(), nullable=True))
    op.add_column("trending_products", sa.Column("shop_rating", sa.Float(), nullable=True))
    op.add_column("trending_products", sa.Column("shop_followers", sa.Integer(), nullable=True))
    op.add_column("trending_products", sa.Column("store_sub_scores", postgresql.JSON(), nullable=True))
    op.add_column("trending_products", sa.Column("experience_scores", postgresql.JSON(), nullable=True))
    op.add_column("trending_products", sa.Column("shop_identity_label", sa.String(120), nullable=True))
    op.add_column("trending_products", sa.Column("ratings_breakdown", postgresql.JSON(), nullable=True))
    op.add_column("trending_products", sa.Column("enriched_at", sa.DateTime(timezone=False), nullable=True))
    op.create_index("ix_trending_products_enriched_at", "trending_products", ["enriched_at"])

    # Product: persist rich detail through to user's library
    op.add_column("products", sa.Column("full_description", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("variants", postgresql.JSON(), nullable=True))
    op.add_column("products", sa.Column("specifications", postgresql.JSON(), nullable=True))
    op.add_column("products", sa.Column("selling_points", postgresql.JSON(), nullable=True))
    op.add_column("products", sa.Column("shop_rating", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("shop_followers", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("shop_identity_label", sa.String(120), nullable=True))
    op.add_column("products", sa.Column("store_sub_scores", postgresql.JSON(), nullable=True))
    op.add_column("products", sa.Column("sold_last_30_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "sold_last_30_days")
    op.drop_column("products", "store_sub_scores")
    op.drop_column("products", "shop_identity_label")
    op.drop_column("products", "shop_followers")
    op.drop_column("products", "shop_rating")
    op.drop_column("products", "selling_points")
    op.drop_column("products", "specifications")
    op.drop_column("products", "variants")
    op.drop_column("products", "full_description")

    op.drop_index("ix_trending_products_enriched_at", table_name="trending_products")
    op.drop_column("trending_products", "enriched_at")
    op.drop_column("trending_products", "ratings_breakdown")
    op.drop_column("trending_products", "shop_identity_label")
    op.drop_column("trending_products", "experience_scores")
    op.drop_column("trending_products", "store_sub_scores")
    op.drop_column("trending_products", "shop_followers")
    op.drop_column("trending_products", "shop_rating")
    op.drop_column("trending_products", "sold_last_30_days")
    op.drop_column("trending_products", "full_description")
    op.drop_column("trending_products", "selling_points")
    op.drop_column("trending_products", "specifications")
    op.drop_column("trending_products", "variants")
    # video_urls owned by migration p7h8 — do not drop here
