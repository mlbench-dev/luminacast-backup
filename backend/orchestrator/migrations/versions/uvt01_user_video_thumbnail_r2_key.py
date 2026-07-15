"""add thumbnail_r2_key to user_video_assets

Revision ID: uvt01_user_video_thumbnail
Revises: pl01_production_level
Create Date: 2026-05-12

Adds a nullable VARCHAR(500) `thumbnail_r2_key` column to
`user_video_assets` so the upload flow can store a pre-rendered JPEG
thumbnail for library tiles and downstream consumers (Publish page,
email previews, share cards).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "uvt01_user_video_thumbnail"
down_revision: Union[str, Sequence[str], None] = "pl01_production_level"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_video_assets",
        sa.Column("thumbnail_r2_key", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_video_assets", "thumbnail_r2_key")
