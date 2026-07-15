"""channels, product_assets, cast expansion

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-01 12:00:00.000000

Phase 1: Add channels, channel_transcripts, product_assets tables.
Expand products, casts, blocks, variants with new columns.
Seed layout template presets (triple).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── channels ──
    op.create_table(
        "channels",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("handle", sa.String(), nullable=False),
        sa.Column("platform_user_id", sa.String(), server_default=""),
        sa.Column("display_name", sa.String(), server_default=""),
        sa.Column("bio", sa.Text(), server_default=""),
        sa.Column("profile_image_key", sa.String(), server_default=""),
        sa.Column("followers_count", sa.Integer(), server_default="0"),
        sa.Column("video_count", sa.Integer(), server_default="0"),
        sa.Column("avg_views", sa.Integer(), server_default="0"),
        sa.Column("stream_key", sa.String(), server_default=""),
        sa.Column("stream_url", sa.String(), server_default=""),
        sa.Column("index_status", sa.String(), server_default="pending"),
        sa.Column("indexed_video_count", sa.Integer(), server_default="0"),
        sa.Column("index_target_count", sa.Integer(), server_default="50"),
        sa.Column("relevant_video_count", sa.Integer(), server_default="0"),
        sa.Column("last_indexed_at", sa.DateTime(), nullable=True),
        sa.Column("voice_profile", sa.JSON(), nullable=True),
        sa.Column("speaker_embedding_key", sa.String(), server_default=""),
        sa.Column("status", sa.String(), server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_channels_user_platform", "channels",
                     ["user_id", "platform", "handle"], unique=True)

    # ── channel_transcripts ──
    op.create_table(
        "channel_transcripts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("channel_id", sa.String(), sa.ForeignKey("channels.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("video_url", sa.String(), server_default=""),
        sa.Column("video_title", sa.String(), server_default=""),
        sa.Column("video_duration_seconds", sa.Float(), server_default="0"),
        sa.Column("video_views", sa.Integer(), server_default="0"),
        sa.Column("video_posted_at", sa.DateTime(), nullable=True),
        sa.Column("is_relevant", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("relevance_reason", sa.String(), server_default=""),
        sa.Column("speech_ratio", sa.Float(), server_default="0"),
        sa.Column("has_main_speaker", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("transcript_text", sa.Text(), server_default=""),
        sa.Column("transcript_language", sa.String(), server_default="en"),
        sa.Column("word_count", sa.Integer(), server_default="0"),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── product_assets ──
    op.create_table(
        "product_assets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), sa.ForeignKey("products.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("r2_key", sa.String(), nullable=False),
        sa.Column("r2_url", sa.String(), server_default=""),
        sa.Column("thumbnail_r2_key", sa.String(), server_default=""),
        sa.Column("duration_seconds", sa.Float(), server_default="0"),
        sa.Column("width", sa.Integer(), server_default="0"),
        sa.Column("height", sa.Integer(), server_default="0"),
        sa.Column("file_size_bytes", sa.Integer(), server_default="0"),
        sa.Column("overlay_config", sa.JSON(), nullable=True),
        sa.Column("generation_prompt", sa.String(), server_default=""),
        sa.Column("generation_model", sa.String(), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Expand products table ──
    op.add_column("products", sa.Column("tiktok_product_id", sa.String(), nullable=True, index=True))
    op.add_column("products", sa.Column("title", sa.String(), nullable=True))
    op.add_column("products", sa.Column("product_url", sa.String(), server_default=""))
    op.add_column("products", sa.Column("current_price", sa.Float(), server_default="0"))
    op.add_column("products", sa.Column("original_price", sa.Float(), server_default="0"))
    op.add_column("products", sa.Column("discount_percent", sa.Float(), server_default="0"))
    op.add_column("products", sa.Column("sales_volume", sa.Integer(), server_default="0"))
    op.add_column("products", sa.Column("rating", sa.Float(), server_default="0"))
    op.add_column("products", sa.Column("review_count", sa.Integer(), server_default="0"))
    op.add_column("products", sa.Column("seller_name", sa.String(), server_default=""))
    op.add_column("products", sa.Column("category", sa.String(), server_default=""))
    op.add_column("products", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("products", sa.Column("cover_image_key", sa.String(), server_default=""))
    op.add_column("products", sa.Column("status", sa.String(), server_default="active"))

    # ── Expand casts table ──
    op.add_column("casts", sa.Column("channel_id", sa.String(), sa.ForeignKey("channels.id"),
                                      nullable=True, index=True))
    op.add_column("casts", sa.Column("layout_config", sa.JSON(), nullable=True))
    op.add_column("casts", sa.Column("progress_percent", sa.Integer(), server_default="0"))
    op.add_column("casts", sa.Column("progress_step", sa.String(), server_default=""))
    op.add_column("casts", sa.Column("total_clips", sa.Integer(), server_default="0"))
    op.add_column("casts", sa.Column("completed_clips", sa.Integer(), server_default="0"))
    op.add_column("casts", sa.Column("base_price", sa.Float(), server_default="14.99"))
    op.add_column("casts", sa.Column("effects_price", sa.Float(), server_default="0"))
    op.add_column("casts", sa.Column("total_price", sa.Float(), server_default="14.99"))
    op.add_column("casts", sa.Column("stripe_payment_id", sa.String(), server_default=""))
    op.add_column("casts", sa.Column("completed_at", sa.DateTime(), nullable=True))

    # ── Expand blocks table ──
    op.add_column("blocks", sa.Column("sort_order", sa.Integer(), server_default="0"))
    op.add_column("blocks", sa.Column("layout_template_ids", sa.JSON(), nullable=True))
    op.add_column("blocks", sa.Column("video_asset_id", sa.String(),
                                       sa.ForeignKey("product_assets.id"), nullable=True))
    op.add_column("blocks", sa.Column("image_asset_id", sa.String(),
                                       sa.ForeignKey("product_assets.id"), nullable=True))
    op.add_column("blocks", sa.Column("overlay_type", sa.String(), nullable=True))
    op.add_column("blocks", sa.Column("overlay_config", sa.JSON(), nullable=True))

    # ── Expand variants table ──
    op.add_column("variants", sa.Column("variant_label", sa.String(), server_default="A"))
    op.add_column("variants", sa.Column("variant_style", sa.String(), server_default=""))
    op.add_column("variants", sa.Column("estimated_duration_seconds", sa.Float(), server_default="0"))
    op.add_column("variants", sa.Column("tts_r2_key", sa.String(), server_default=""))
    op.add_column("variants", sa.Column("tts_duration_seconds", sa.Float(), server_default="0"))
    op.add_column("variants", sa.Column("video_r2_key", sa.String(), server_default=""))
    op.add_column("variants", sa.Column("video_r2_url", sa.String(), server_default=""))

    # Add BlockType enums for new types (testimonial, qa)
    # Alembic raw SQL for extending enum — postgres specific
    op.execute("ALTER TYPE blocktype ADD VALUE IF NOT EXISTS 'testimonial'")
    op.execute("ALTER TYPE blocktype ADD VALUE IF NOT EXISTS 'qa'")

    # Add VariantStatus enums
    op.execute("ALTER TYPE variantstatus ADD VALUE IF NOT EXISTS 'draft'")
    op.execute("ALTER TYPE variantstatus ADD VALUE IF NOT EXISTS 'completed'")

    # Seed "Triple" layout preset
    op.execute("""
        INSERT INTO layout_templates (id, user_id, name, config, is_preset) VALUES
        ('lt_preset_triple', NULL, 'Triple', '{"avatar": {"x": 0, "y": 0, "width": 90, "height": 480}, "product": {"x": 90, "y": 0, "width": 90, "height": 480}, "product_image": {"x": 180, "y": 0, "width": 90, "height": 480}, "text_overlay": "bottom_bar", "canvas_width": 270, "canvas_height": 480}', true)
        ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    # Drop new variant columns
    op.drop_column("variants", "video_r2_url")
    op.drop_column("variants", "video_r2_key")
    op.drop_column("variants", "tts_duration_seconds")
    op.drop_column("variants", "tts_r2_key")
    op.drop_column("variants", "estimated_duration_seconds")
    op.drop_column("variants", "variant_style")
    op.drop_column("variants", "variant_label")

    # Drop new block columns
    op.drop_column("blocks", "overlay_config")
    op.drop_column("blocks", "overlay_type")
    op.drop_column("blocks", "image_asset_id")
    op.drop_column("blocks", "video_asset_id")
    op.drop_column("blocks", "layout_template_ids")
    op.drop_column("blocks", "sort_order")

    # Drop new cast columns
    op.drop_column("casts", "completed_at")
    op.drop_column("casts", "stripe_payment_id")
    op.drop_column("casts", "total_price")
    op.drop_column("casts", "effects_price")
    op.drop_column("casts", "base_price")
    op.drop_column("casts", "completed_clips")
    op.drop_column("casts", "total_clips")
    op.drop_column("casts", "progress_step")
    op.drop_column("casts", "progress_percent")
    op.drop_column("casts", "layout_config")
    op.drop_column("casts", "channel_id")

    # Drop new product columns
    op.drop_column("products", "status")
    op.drop_column("products", "cover_image_key")
    op.drop_column("products", "tags")
    op.drop_column("products", "category")
    op.drop_column("products", "seller_name")
    op.drop_column("products", "review_count")
    op.drop_column("products", "rating")
    op.drop_column("products", "sales_volume")
    op.drop_column("products", "discount_percent")
    op.drop_column("products", "original_price")
    op.drop_column("products", "current_price")
    op.drop_column("products", "product_url")
    op.drop_column("products", "title")
    op.drop_column("products", "tiktok_product_id")

    op.drop_table("product_assets")
    op.drop_table("channel_transcripts")
    op.drop_table("channels")
