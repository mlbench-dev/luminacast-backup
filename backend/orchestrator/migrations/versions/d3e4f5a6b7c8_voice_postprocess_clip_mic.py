"""voice post-process: variants.tts_lipsync_r2_key + avatars.clip_mic_enabled

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-05-12

PR #65 wires broadcast-quality voice post-processing into every TTS
path. TTS output now yields two files per variant:

  * tts_r2_key           — 44.1 kHz mono MP3 192 kbps (the master fed
                           to the final compose audio remux added in
                           PR #64).
  * tts_lipsync_r2_key   — 16 kHz mono WAV (fed to the lipsync engine,
                           which requires that exact format).

The new ``avatars.clip_mic_enabled`` toggles the EQ profile between
phone-mic (default OFF: natural, lighter compand) and clip-on lav
(ON: warm proximity, tighter compand). The flag is also injected as
a mic-style suffix into the voice_description sent to the TTS engine
so the model already generates audio that sounds like the chosen mic.
"""
from alembic import op
import sqlalchemy as sa


revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS so the migration is idempotent against
    # databases where the columns were added by the lifespan column-additions
    # block on a previous deploy.
    op.execute(
        "ALTER TABLE variants "
        "ADD COLUMN IF NOT EXISTS tts_lipsync_r2_key TEXT"
    )
    op.execute(
        "ALTER TABLE avatars "
        "ADD COLUMN IF NOT EXISTS clip_mic_enabled BOOLEAN "
        "NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE avatars DROP COLUMN IF EXISTS clip_mic_enabled")
    op.execute("ALTER TABLE variants DROP COLUMN IF EXISTS tts_lipsync_r2_key")
