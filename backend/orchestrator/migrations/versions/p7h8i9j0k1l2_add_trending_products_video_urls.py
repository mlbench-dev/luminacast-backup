"""Add trending_products video_urls column

Revision ID: p7h8i9j0k1l2
Revises: o6g7h8i9j0k1
Create Date: 2026-04-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "p7h8i9j0k1l2"
down_revision: Union[str, None] = "o6g7h8i9j0k1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trending_products", sa.Column("video_urls", postgresql.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("trending_products", "video_urls")
