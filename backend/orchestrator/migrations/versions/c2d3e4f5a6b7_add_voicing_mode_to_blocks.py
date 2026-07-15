"""add voicing_mode to blocks

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-09

Why: Opus emits a `voicing_mode` field on every avatar_action /
generated_video block (one of tts_dialogue / prosody_only /
motion_sfx_only) that controls whether the renderer runs TTS, lipsync,
or pure silent-action with motion SFX. Until this migration the value
was discarded — there was nowhere to store it on `blocks`. The renderer
gates lipsync + audio mux on this column.
"""
from alembic import op
import sqlalchemy as sa


revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "blocks",
        sa.Column(
            "voicing_mode",
            sa.String(length=24),
            nullable=False,
            server_default="tts_dialogue",
        ),
    )


def downgrade() -> None:
    op.drop_column("blocks", "voicing_mode")
