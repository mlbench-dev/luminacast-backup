"""Drop casts.debug_face_ref_override_key (dev-only avatar-layout-fix A/B tool removed)

Revision ID: fc09_drop_debug_face_ref
Revises: fc08_avatar_layout
Create Date: 2026-09-22

The layout picker at avatar-creation time (Avatar.layout, shipped in
fc08_avatar_layout) replaced the need for this dev-only escape hatch, and
the tool itself (routers/dev_avatar_layout_fix.py, services/avatar_layout_fix.py,
the SetupPhase.tsx / EditAvatarPage.tsx dev panels) has been removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc09_drop_debug_face_ref"
down_revision: Union[str, None] = "fc08_avatar_layout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "debug_face_ref_override_key" in columns:
        op.drop_column("casts", "debug_face_ref_override_key")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "debug_face_ref_override_key" not in columns:
        op.add_column(
            "casts",
            sa.Column("debug_face_ref_override_key", sa.String(length=500), nullable=True),
        )
