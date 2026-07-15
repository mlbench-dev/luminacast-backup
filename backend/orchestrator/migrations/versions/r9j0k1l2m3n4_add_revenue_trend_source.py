"""add revenue_trend_source to trending_products

Revision ID: r9j0k1l2m3n4
Revises: q8i9j0k1l2m3
Create Date: 2026-04-07

"""
from alembic import op
import sqlalchemy as sa

revision = "r9j0k1l2m3n4"
down_revision = "q8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trending_products",
        sa.Column("revenue_trend_source", sa.String(), nullable=True, server_default="empty"),
    )


def downgrade() -> None:
    op.drop_column("trending_products", "revenue_trend_source")
