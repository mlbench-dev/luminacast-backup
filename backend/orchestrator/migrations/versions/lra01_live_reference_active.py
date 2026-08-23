"""live_references.is_active — let a user switch which past-live style is used

Revision ID: lra01_live_reference_active
Revises: subp01_subscription_payments
Create Date: 2026-08-23

Previously the script generator always used whichever LiveReference was
most recently uploaded+assessed for a scope (avatar_id or cast_id) — see
engine.live_style.resolve_active_assessment. Switching back to an earlier
recording's style meant re-uploading a duplicate of it, paying for
transcription/assessment all over again. This adds an explicit is_active
flag: exactly one "assessed" row per scope can be active at a time
(enforced by the two partial unique indexes below), and the user can flip
it via POST /live-references/{id}/activate instead of re-uploading.

Backfills is_active=true for the most-recently-created assessed row per
scope so existing accounts see no behavior change on deploy — the same row
that would have won under the old "most recent" rule is the one that
starts active.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lra01_live_reference_active"
down_revision: Union[str, None] = "subp01_subscription_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("live_references")}

    if "is_active" not in existing_columns:
        op.add_column(
            "live_references",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        )

    # Backfill: activate the most-recently-created assessed row per scope —
    # matches what resolve_active_assessment would have picked before this
    # column existed, so nobody's active style silently changes on deploy.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(avatar_id, ''), COALESCE(cast_id, '')
                       ORDER BY created_at DESC
                   ) AS rn
            FROM live_references
            WHERE status = 'assessed'
        )
        UPDATE live_references
        SET is_active = true
        FROM ranked
        WHERE live_references.id = ranked.id AND ranked.rn = 1
        """
    )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("live_references")}
    if "ux_live_references_active_avatar" not in existing_indexes:
        op.create_index(
            "ux_live_references_active_avatar",
            "live_references", ["avatar_id"], unique=True,
            postgresql_where=sa.text("is_active AND avatar_id IS NOT NULL"),
        )
    if "ux_live_references_active_cast" not in existing_indexes:
        op.create_index(
            "ux_live_references_active_cast",
            "live_references", ["cast_id"], unique=True,
            postgresql_where=sa.text("is_active AND cast_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("live_references")}
    if "ux_live_references_active_cast" in existing_indexes:
        op.drop_index("ux_live_references_active_cast", table_name="live_references")
    if "ux_live_references_active_avatar" in existing_indexes:
        op.drop_index("ux_live_references_active_avatar", table_name="live_references")
    existing_columns = {c["name"] for c in inspector.get_columns("live_references")}
    if "is_active" in existing_columns:
        op.drop_column("live_references", "is_active")
