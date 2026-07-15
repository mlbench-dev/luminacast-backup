"""create ai_generated_photos table

Revision ID: sdxl01_ai_gen_photos
Revises: ap02_avatar_gender
Create Date: 2026-04-14

"""
from alembic import op
import sqlalchemy as sa

revision = "sdxl01_ai_gen_photos"
down_revision = "ap02_avatar_gender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_generated_photos (
            id VARCHAR(40) PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            r2_key VARCHAR(512) NOT NULL,
            prompt TEXT NOT NULL,
            negative_prompt TEXT,
            width INT NOT NULL,
            height INT NOT NULL,
            seed BIGINT,
            engine_used VARCHAR(64) NOT NULL,
            tier INT NOT NULL,
            model_variant VARCHAR(64),
            cost_usd DECIMAL(10, 4),
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_generated_photos_user_not_deleted
            ON ai_generated_photos(user_id, created_at DESC)
            WHERE deleted_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_generated_photos")
