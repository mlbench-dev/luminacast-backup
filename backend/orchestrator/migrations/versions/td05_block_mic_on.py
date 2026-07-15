"""add mic_on to blocks (Step 5 — template defaults)

Revision ID: td05_block_mic_on
Revises: lt03_cast_layout_template_id
Create Date: 2026-06-14

Adds a per-block ``blocks.mic_on`` (BOOLEAN, nullable) flag. Step 5 stamps it
from the cast's layout template (``template.config.voice.mic == "on"``) when the
block leaves it unset, so later steps (the audio-chain wiring in Step 7 and the
FLUX mic-on look variant in Step 8) have a single per-block source of truth.

Nullable on purpose: NULL means "unset / inherit" — existing blocks and any
block whose template is unknown keep working unchanged. Indexed so later steps
can cheaply filter mic-on vs mic-off blocks.

Parented on ``lt03_cast_layout_template_id`` (the head shipped in Step 3, which
added ``casts.layout_template_id``). One migration, one head.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "td05_block_mic_on"
down_revision: Union[str, Sequence[str], None] = "lt03_cast_layout_template_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "blocks",
        sa.Column("mic_on", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_blocks_mic_on",
        "blocks",
        ["mic_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_blocks_mic_on", table_name="blocks")
    op.drop_column("blocks", "mic_on")
