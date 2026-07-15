"""add clone_tiktok_scans and clone_tiktok_videos tables

Revision ID: ab02_clone_scout
Revises: aa01_render_jobs
Create Date: 2026-04-13

"""
from alembic import op
import sqlalchemy as sa

revision = 'ab02_clone_scout'
down_revision = 'aa01_render_jobs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS clone_tiktok_scans (
            id VARCHAR(40) PRIMARY KEY,
            user_id VARCHAR(40) NOT NULL REFERENCES users(id),
            tiktok_handle VARCHAR(200) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            videos_found INTEGER DEFAULT 0,
            videos_analyzed INTEGER DEFAULT 0,
            videos_with_full_body INTEGER DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMP,
            error TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_clone_tiktok_scans_user_id ON clone_tiktok_scans (user_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS clone_tiktok_videos (
            id VARCHAR(40) PRIMARY KEY,
            scan_id VARCHAR(40) NOT NULL REFERENCES clone_tiktok_scans(id),
            tiktok_video_id VARCHAR(200) NOT NULL,
            tiktok_url VARCHAR(500) NOT NULL,
            thumbnail_url VARCHAR(500),
            duration_seconds FLOAT,
            download_url VARCHAR(1000),
            has_full_body BOOLEAN DEFAULT false,
            full_body_segments JSONB DEFAULT '[]'::jsonb,
            total_full_body_duration_ms INTEGER DEFAULT 0,
            first_full_body_frame_url VARCHAR(500),
            analyzed_at TIMESTAMP,
            status VARCHAR(20) DEFAULT 'pending'
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_clone_tiktok_videos_scan_id ON clone_tiktok_videos (scan_id)")

    # Add avatar columns if missing (most exist from v3)
    for col, coltype, default in [
        ("target_audience", "JSONB", None),
        ("body_description", "TEXT", None),
        ("style_preset", "VARCHAR(50)", None),
    ]:
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'avatars' AND column_name = '{col}'
                ) THEN
                    ALTER TABLE avatars ADD COLUMN {col} {coltype};
                END IF;
            END
            $$;
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS clone_tiktok_videos")
    op.execute("DROP TABLE IF EXISTS clone_tiktok_scans")
