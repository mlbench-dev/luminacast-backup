"""Add avatar runpod_job_id and active_phase columns

Revision ID: o6g7h8i9j0k1
Revises: n5f6g7h8i9j0
Create Date: 2026-04-06
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "o6g7h8i9j0k1"
down_revision: Union[str, None] = "n5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("avatars", sa.Column("runpod_job_id", sa.String(), nullable=True))

    avatar_phase_enum = sa.Enum("image", "voice", "render", "ready", "failed", name="avatarphase")
    avatar_phase_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("avatars", sa.Column(
        "active_phase",
        avatar_phase_enum,
        nullable=False,
        server_default="image",
    ))
    op.create_index("ix_avatars_active_phase", "avatars", ["active_phase"])

    # Backfill: avatarstatus enum values are UPPERCASE in the DB
    op.execute(sa.text(
        "UPDATE avatars SET active_phase = 'ready' "
        "WHERE status::text IN ('READY', 'APPROVED')"
    ))
    op.execute(sa.text(
        "UPDATE avatars SET active_phase = 'failed' "
        "WHERE status::text = 'FAILED'"
    ))
    op.execute(sa.text(
        "UPDATE avatars SET active_phase = 'voice' "
        "WHERE status::text = 'FACE_CANDIDATES_READY' AND face_ref_key IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE avatars SET active_phase = 'image' "
        "WHERE status::text IN ('PROCESSING', 'FACE_CANDIDATES_READY') AND face_ref_key IS NULL"
    ))


def downgrade() -> None:
    op.drop_index("ix_avatars_active_phase", "avatars")
    op.drop_column("avatars", "active_phase")
    sa.Enum(name="avatarphase").drop(op.get_bind(), checkfirst=True)
    op.drop_column("avatars", "runpod_job_id")
