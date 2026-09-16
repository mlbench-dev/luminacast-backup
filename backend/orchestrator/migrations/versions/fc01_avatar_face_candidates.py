"""Add avatars.face_candidates (AI-generated face batch, for Face step resume)

Revision ID: fc01_avatar_face_candidates
Revises: brl01_cast_broll_media_source
Create Date: 2026-09-16

The AI avatar wizard's Face step generated 8 candidate face URLs but never
persisted them — only the single final selection (face_ref_key). Navigating
away and back, or refreshing, re-triggered generation from scratch. This
column stores the generated batch so it can be restored on remount instead.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc01_avatar_face_candidates"
down_revision: Union[str, None] = "brl01_cast_broll_media_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "face_candidates" not in columns:
        op.add_column(
            "avatars",
            sa.Column("face_candidates", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "face_candidates" in columns:
        op.drop_column("avatars", "face_candidates")
