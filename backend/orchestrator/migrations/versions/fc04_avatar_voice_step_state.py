"""Add avatars voice-step columns (restore-on-refresh for the Voice step)

Revision ID: fc04_avatar_voice_step_state
Revises: fc03_avatar_edited_face_versions
Create Date: 2026-09-16

Same bug class as the Face step: voice_description/test_speech/language/
accent/voice_previews were pure local React state with no persistence, so
refreshing mid-Voice-step regenerated the description from scratch and reset
language/accent to defaults, discarding the user's picks.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc04_avatar_voice_step_state"
down_revision: Union[str, None] = "fc03_avatar_edited_face_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = [
    ("voice_description", sa.Text(), True, None),
    ("voice_test_speech", sa.Text(), True, None),
    ("voice_language", sa.String(length=20), True, None),
    ("voice_accent", sa.String(length=20), True, None),
    ("voice_desc_overridden", sa.Boolean(), False, "false"),
    ("voice_previews", sa.JSON(), True, None),
    ("selected_voice_preview_idx", sa.Integer(), True, None),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    for name, col_type, nullable, server_default in _NEW_COLUMNS:
        if name not in columns:
            op.add_column(
                "avatars",
                sa.Column(name, col_type, nullable=nullable, server_default=server_default),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("avatars")}

    for name, _col_type, _nullable, _server_default in _NEW_COLUMNS:
        if name in columns:
            op.drop_column("avatars", name)
