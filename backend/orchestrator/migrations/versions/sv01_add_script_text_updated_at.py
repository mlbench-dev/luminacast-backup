"""Add script_text_updated_at to variants

The stale-TTS defense-in-depth check in tasks/cast_render.py
(_ensure_fresh_tts_for_block) needs to know when the SCRIPT was last
edited, so it can tell "the script changed after this audio was made"
apart from "something else on this row (captions, sfx timings, ...) was
saved after this audio was made". The generic `updated_at` column bumps
on every write regardless of which column changed, which made the check
fire on the normal generate-audio-then-generate-captions sequence every
single time — silently discarding good audio and replacing it with a
fresh (differently-paced) take. This column is set ONLY when
`script_text` itself changes (see the `@validates` hook on the Variant
model), giving the staleness check a signal that means what it says.

Revision ID: sv01_add_script_text_updated_at
Revises: fc10_cast_auto_cast
Create Date: 2026-09-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "sv01_add_script_text_updated_at"
down_revision: Union[str, None] = "fc10_cast_auto_cast"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "variants",
        sa.Column("script_text_updated_at", sa.DateTime(), nullable=True),
    )
    # Backfill existing rows from updated_at (best available signal) so a
    # variant that already has a script isn't treated as "never edited"
    # (which would skip the staleness check entirely) right after upgrade.
    op.execute(
        "UPDATE variants SET script_text_updated_at = updated_at "
        "WHERE script_text IS NOT NULL AND script_text_updated_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("variants", "script_text_updated_at")
