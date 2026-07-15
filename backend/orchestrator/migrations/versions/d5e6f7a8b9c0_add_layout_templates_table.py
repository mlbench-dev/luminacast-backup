"""add layout_templates table

Revision ID: d5e6f7a8b9c0
Revises: c4a7e8f91b23
Create Date: 2026-03-31 14:00:00.000000

UI Overhaul: Layout templates for scene editor — presets + user custom layouts.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4a7e8f91b23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "layout_templates",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), index=True, nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_preset", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # Seed 6 preset layouts
    op.execute("""
        INSERT INTO layout_templates (id, user_id, name, config, is_preset) VALUES
        ('lt_preset_side_by_side', NULL, 'Side by side', '{"avatar": {"x": 0, "y": 0, "width": 108, "height": 480}, "product": {"x": 108, "y": 0, "width": 162, "height": 480}, "text_overlay": "bottom_bar", "canvas_width": 270, "canvas_height": 480}', true),
        ('lt_preset_50_50', NULL, '50/50 split', '{"avatar": {"x": 0, "y": 0, "width": 135, "height": 480}, "product": {"x": 135, "y": 0, "width": 135, "height": 480}, "text_overlay": "bottom_bar", "canvas_width": 270, "canvas_height": 480}', true),
        ('lt_preset_pip_avatar', NULL, 'PIP avatar', '{"avatar": {"x": 170, "y": 360, "width": 90, "height": 110}, "product": {"x": 0, "y": 0, "width": 270, "height": 480}, "text_overlay": "bottom_bar", "canvas_width": 270, "canvas_height": 480}', true),
        ('lt_preset_pip_product', NULL, 'PIP product', '{"avatar": {"x": 0, "y": 0, "width": 270, "height": 480}, "product": {"x": 170, "y": 360, "width": 90, "height": 110}, "text_overlay": "bottom_bar", "canvas_width": 270, "canvas_height": 480}', true),
        ('lt_preset_avatar_only', NULL, 'Avatar only', '{"avatar": {"x": 0, "y": 0, "width": 270, "height": 480}, "product": {"x": 0, "y": 0, "width": 0, "height": 0}, "text_overlay": "none", "canvas_width": 270, "canvas_height": 480}', true),
        ('lt_preset_top_bottom', NULL, 'Top/bottom', '{"avatar": {"x": 0, "y": 0, "width": 270, "height": 240}, "product": {"x": 0, "y": 240, "width": 270, "height": 240}, "text_overlay": "bottom_bar", "canvas_width": 270, "canvas_height": 480}', true)
    """)


def downgrade() -> None:
    op.drop_table("layout_templates")
