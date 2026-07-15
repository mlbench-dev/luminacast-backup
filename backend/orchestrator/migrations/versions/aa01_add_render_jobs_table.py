"""add render_jobs table for honest render status tracking

Revision ID: aa01_render_jobs
Revises: z7a8b9c0d1e2
Create Date: 2026-04-13

"""
from alembic import op
import sqlalchemy as sa

revision = 'aa01_render_jobs'
down_revision = 'z7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS render_jobs (
            id VARCHAR(40) PRIMARY KEY,
            job_type VARCHAR(30) NOT NULL,
            provider VARCHAR(40) NOT NULL,
            external_job_id VARCHAR(200),
            avatar_id VARCHAR(40) REFERENCES avatars(id),
            cast_id VARCHAR(40) REFERENCES casts(id),
            variant_id VARCHAR(40) REFERENCES variants(id),
            state VARCHAR(20) NOT NULL DEFAULT 'QUEUED',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            queued_at TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            failed_at TIMESTAMP,
            error_message TEXT,
            last_status_check_at TIMESTAMP,
            last_state_change_at TIMESTAMP,
            progress_percent INTEGER,
            metadata JSONB DEFAULT '{}'::jsonb
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_render_jobs_state ON render_jobs (state)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_render_jobs_avatar_id ON render_jobs (avatar_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_render_jobs_cast_id ON render_jobs (cast_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_render_jobs_variant_id ON render_jobs (variant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_render_jobs_external_job_id ON render_jobs (external_job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_render_jobs_job_type ON render_jobs (job_type)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS render_jobs")
