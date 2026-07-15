"""add voice corpus table

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = "x5y6z7a8b9c0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS creator_voice_corpus (
            id VARCHAR(40) PRIMARY KEY,
            avatar_id VARCHAR(40) NOT NULL REFERENCES avatars(id),
            source_type VARCHAR(20) NOT NULL,
            source_url VARCHAR(1000),
            audio_r2_key VARCHAR(500),
            transcript TEXT,
            duration_seconds FLOAT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_creator_voice_corpus_avatar_id
        ON creator_voice_corpus (avatar_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS creator_voice_corpus")
