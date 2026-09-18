"""Add avatars.layout

Revision ID: fc08_avatar_layout
Revises: fc07_cast_debug_face_ref
Create Date: 2026-09-18

Layout ("9:16" | "16:9" | "1:1" | "4:5") the avatar's face_ref_key was
generated/cropped for, picked by the user at avatar-creation time. Lets
cast Setup's avatar picker only offer avatars matching the cast's layout,
so the avatar/layout mismatch bug (letterbox+blur fallback at render time)
can't happen for newly-created avatars. Nullable — existing avatars keep
their current NULL value and are treated as a wildcard (shown for every
layout) by the picker.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc08_avatar_layout"
down_revision: Union[str, None] = "fc07_cast_debug_face_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "layout" not in columns:
        op.add_column(
            "avatars",
            sa.Column("layout", sa.String(length=10), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "layout" in columns:
        op.drop_column("avatars", "layout")
