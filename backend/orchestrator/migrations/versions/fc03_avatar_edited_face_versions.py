"""Add avatars.edited_face_versions (Face-step "Edit this face" history)

Revision ID: fc03_avatar_edited_face_versions
Revises: fc02_avatar_selected_face_url
Create Date: 2026-09-16

Each successful /ai/edit-face call only returned the edited URL to the
client, which kept it in local component state — a refresh lost the edit
entirely, reverting to the unedited base candidate even though
face_candidates/selected_face_url now survive. This column accumulates the
edit history for the currently selected base face so it can be restored.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc03_avatar_edited_face_versions"
down_revision: Union[str, None] = "fc02_avatar_selected_face_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "edited_face_versions" not in columns:
        op.add_column(
            "avatars",
            sa.Column("edited_face_versions", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    if "edited_face_versions" in columns:
        op.drop_column("avatars", "edited_face_versions")
