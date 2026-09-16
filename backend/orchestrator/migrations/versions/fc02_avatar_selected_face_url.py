"""Add avatars.selected_face_url (which Face-step candidate is highlighted)

Revision ID: fc02_avatar_selected_face_url
Revises: fc01_avatar_face_candidates
Create Date: 2026-09-16

Clicking a face thumbnail in the Face step only set local component state,
so a refresh/navigate-away-and-back lost the highlighted selection even
though the candidate grid itself now survives (face_candidates). This is
distinct from face_ref_key, which is only written once the user clicks
"Continue to voice" — this column tracks the in-progress pick before that.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc02_avatar_selected_face_url"
down_revision: Union[str, None] = "fc01_avatar_face_candidates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "selected_face_url" not in columns:
        op.add_column(
            "avatars",
            sa.Column("selected_face_url", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "selected_face_url" in columns:
        op.drop_column("avatars", "selected_face_url")
