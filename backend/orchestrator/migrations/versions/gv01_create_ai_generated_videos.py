"""create ai_generated_videos table

Revision ID: gv01_ai_gen_videos
Revises: sdxl01_ai_gen_photos
Create Date: 2026-04-14

"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "gv01_ai_gen_videos"
down_revision: Union[str, None] = "sdxl01_ai_gen_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_generated_videos (
            id VARCHAR(40) PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            r2_key VARCHAR(512) NOT NULL,
            thumbnail_r2_key VARCHAR(512),
            prompt TEXT NOT NULL,
            negative_prompt TEXT,
            engine_used VARCHAR(64) NOT NULL,
            mode VARCHAR(32) NOT NULL,
            duration_seconds INT NOT NULL,
            aspect_ratio VARCHAR(16) NOT NULL,
            camera_preset VARCHAR(64),
            reference_image_r2_key VARCHAR(512),
            seed BIGINT,
            cost_usd DECIMAL(10, 4),
            batch_id VARCHAR(64),
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_generated_videos_user_not_deleted
            ON ai_generated_videos(user_id, created_at DESC)
            WHERE deleted_at IS NULL;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_generated_videos_batch
            ON ai_generated_videos(batch_id)
            WHERE deleted_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_generated_videos")
