"""avatar_looks.mic_visible becomes nullable — NULL means "never deliberately set"

Revision ID: alm01_avatar_look_mic_null
Revises: lra01_live_reference_active
Create Date: 2026-08-23

Part of scene-aware voice filters: a look's own mic_visible is about to
outrank the block's template-driven mic_on (see
services.mic_presets.resolve_scene_voice_settings and
tasks.cast_render._maybe_mic_on). That only works if "never chosen" is
distinguishable from "explicitly off" — the column was previously
NOT NULL DEFAULT false, so every existing look (and every look created
before a user ever sees the new mic-visible toggle) already carries a
concrete False, which would have looked exactly like a deliberate choice
and silently overridden every template's mic_on the moment this shipped.

Backfilling every existing row to NULL restores today's real behavior
(block/avatar decides) for all pre-existing data; only a real True/False
written from now on via the new scene-creation UI should ever win.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "alm01_avatar_look_mic_null"
down_revision: Union[str, None] = "lra01_live_reference_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns("avatar_looks")}

    if columns.get("mic_visible", {}).get("nullable") is False:
        op.alter_column(
            "avatar_looks", "mic_visible",
            existing_type=sa.Boolean(),
            nullable=True,
            server_default=None,
        )
        op.execute("UPDATE avatar_looks SET mic_visible = NULL")


def downgrade() -> None:
    op.execute("UPDATE avatar_looks SET mic_visible = false WHERE mic_visible IS NULL")
    op.alter_column(
        "avatar_looks", "mic_visible",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default="false",
    )
