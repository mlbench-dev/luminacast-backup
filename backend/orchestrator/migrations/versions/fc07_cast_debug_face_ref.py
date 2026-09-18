"""Add casts.debug_face_ref_override_key (dev-only avatar-layout-fix A/B tool)

Revision ID: fc07_cast_debug_face_ref
Revises: fc06_product_visual_desc
Create Date: 2026-09-18

Dev-only escape hatch for routers/dev_avatar_layout_fix.py — when set, the
render pipeline uses this R2 key directly as the talking-head/PIP face
reference for that one cast, bypassing normal resolution entirely, so two
test casts can be pinned to two different avatar-layout-fix candidates
(crop vs. AI-generate) for a real side-by-side render comparison. Never
touched by normal cast creation/update flows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc07_cast_debug_face_ref"
down_revision: Union[str, None] = "fc06_product_visual_desc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "debug_face_ref_override_key" not in columns:
        op.add_column(
            "casts",
            sa.Column("debug_face_ref_override_key", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "debug_face_ref_override_key" in columns:
        op.drop_column("casts", "debug_face_ref_override_key")
