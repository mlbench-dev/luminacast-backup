"""Add trending_products table for product discovery cache

Revision ID: g8a9b0c1d2e3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-03

Product Discovery Hub: cache table for trending TikTok Shop products.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'g8a9b0c1d2e3'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trending_products',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tiktok_product_id', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), server_default=''),
        sa.Column('product_url', sa.String(), server_default=''),

        sa.Column('current_price', sa.Float(), server_default='0'),
        sa.Column('original_price', sa.Float(), server_default='0'),
        sa.Column('discount_percent', sa.Float(), server_default='0'),

        sa.Column('revenue_cents', sa.Integer(), server_default='0'),
        sa.Column('revenue_growth_rate', sa.Float(), server_default='0'),
        sa.Column('items_sold', sa.Integer(), server_default='0'),
        sa.Column('avg_unit_price', sa.Float(), server_default='0'),

        sa.Column('rating', sa.Float(), server_default='0'),
        sa.Column('review_count', sa.Integer(), server_default='0'),

        sa.Column('seller_name', sa.String(), server_default=''),
        sa.Column('seller_id', sa.String(), server_default=''),

        sa.Column('commission_rate', sa.Float(), server_default='0'),
        sa.Column('is_affiliate', sa.Boolean(), server_default='false'),

        sa.Column('creator_count', sa.Integer(), server_default='0'),

        sa.Column('category', sa.String(), server_default=''),
        sa.Column('subcategory', sa.String(), server_default=''),

        sa.Column('cover_image_url', sa.String(), server_default=''),
        sa.Column('cover_image_r2_key', sa.String(), server_default=''),

        sa.Column('section', sa.String(), server_default=''),
        sa.Column('rank_position', sa.Integer(), server_default='0'),

        sa.Column('revenue_trend', sa.JSON(), nullable=True),

        sa.Column('region', sa.String(), server_default='US'),
        sa.Column('scraped_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),

        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_index('ix_trending_products_tiktok_product_id', 'trending_products', ['tiktok_product_id'], unique=True)
    op.create_index('ix_trending_products_section', 'trending_products', ['section'])
    op.create_index('ix_trending_products_category', 'trending_products', ['category'])
    op.create_index('ix_trending_products_region', 'trending_products', ['region'])
    op.create_index('ix_trending_section_rank', 'trending_products', ['section', 'rank_position'])
    op.create_index('ix_trending_revenue', 'trending_products', ['revenue_cents'])
    op.create_index('ix_trending_items_sold', 'trending_products', ['items_sold'])
    op.create_index('ix_trending_commission', 'trending_products', ['commission_rate'])


def downgrade() -> None:
    op.drop_index('ix_trending_commission', table_name='trending_products')
    op.drop_index('ix_trending_items_sold', table_name='trending_products')
    op.drop_index('ix_trending_revenue', table_name='trending_products')
    op.drop_index('ix_trending_section_rank', table_name='trending_products')
    op.drop_index('ix_trending_products_region', table_name='trending_products')
    op.drop_index('ix_trending_products_category', table_name='trending_products')
    op.drop_index('ix_trending_products_section', table_name='trending_products')
    op.drop_index('ix_trending_products_tiktok_product_id', table_name='trending_products')
    op.drop_table('trending_products')
