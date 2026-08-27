"""Add casts.broll_media_source (default visual source for product b-roll blocks)

Revision ID: brl01_cast_broll_media_source
Revises: alm01_avatar_look_mic_null
Create Date: 2026-08-25

Setup-tab default for stock_photo/stock_video blocks that have a product
attached and no per-block override: "stock" (Pexels, existing default
behavior) or "ai_generated" (FLUX Kontext / Kling product-only generation,
see services/product_ai_media.py). Per-block overrides continue to use the
existing Block.video_asset_id / image_asset_id columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "brl01_cast_broll_media_source"
down_revision: Union[str, None] = "alm01_avatar_look_mic_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "broll_media_source" not in columns:
        op.add_column(
            "casts",
            sa.Column(
                "broll_media_source", sa.String(length=20),
                nullable=False, server_default="stock",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "broll_media_source" in columns:
        op.drop_column("casts", "broll_media_source")
