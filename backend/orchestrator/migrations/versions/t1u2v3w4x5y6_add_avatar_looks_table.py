"""add avatar_looks table and block avatar_look_id with backfill

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = 't1u2v3w4x5y6'
down_revision = 's0t1u2v3w4x5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: table may already exist from raw SQL
    op.execute("""
        CREATE TABLE IF NOT EXISTS avatar_looks (
            id VARCHAR(40) PRIMARY KEY,
            avatar_id VARCHAR(40) NOT NULL REFERENCES avatars(id),
            name VARCHAR(200) NOT NULL,
            face_ref_key VARCHAR(500),
            background_prompt TEXT,
            is_default BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Create index if not exists
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_avatar_looks_avatar_id ON avatar_looks (avatar_id)
    """)

    # Add avatar_look_id to blocks if not exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'blocks' AND column_name = 'avatar_look_id'
            ) THEN
                ALTER TABLE blocks ADD COLUMN avatar_look_id VARCHAR(40) REFERENCES avatar_looks(id);
            END IF;
        END
        $$;
    """)

    # Backfill: every existing avatar with face_ref_key gets a default look if missing
    op.execute("""
        INSERT INTO avatar_looks (id, avatar_id, name, face_ref_key, is_default, status, created_at)
        SELECT
            'al_default_' || a.id,
            a.id,
            'Original',
            a.face_ref_key,
            true,
            'ready',
            COALESCE(a.created_at, NOW())
        FROM avatars a
        WHERE a.face_ref_key IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM avatar_looks al WHERE al.avatar_id = a.id
          )
    """)

    # Backfill: every existing block gets pointed at its avatar's default look
    op.execute("""
        UPDATE blocks b
        SET avatar_look_id = (
            SELECT al.id FROM avatar_looks al
            JOIN casts c ON c.id = b.cast_id
            WHERE al.avatar_id = c.avatar_id AND al.is_default = true
            LIMIT 1
        )
        WHERE b.avatar_look_id IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE blocks DROP COLUMN IF EXISTS avatar_look_id
    """)
    op.execute("""
        DROP TABLE IF EXISTS avatar_looks
    """)
