"""teams: TeamMember status/invite columns, Cast approval workflow, User.last_workspace_id

Revision ID: m28_teams_and_approval_status
Revises: m27_unify_heads
Create Date: 2026-08-10 00:00:00.000000

Adds the columns needed for the Teams multi-user-workspace feature:
  - team_members: status/invited_email/accepted_at/revoked_at, and makes
    user_id nullable (a fresh invite has no User row yet).
  - casts: approval_status + submitted/approved tracking — a workflow
    fully separate from the existing `status` pipeline column.
  - users: last_workspace_id (switcher hint, no FK).

Column-existence-guarded throughout: the `team_members` table already
exists in live environments but was never actually CREATEd by any prior
Alembic migration (only 88c51d3c92fd ALTERs it) — its origin predates
this migration chain. Guarding each add avoids failing in an environment
where some of these columns already happen to exist.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m28_teams_and_approval_status"
down_revision: Union[str, None] = "m27_unify_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── team_members ──
    if inspector.has_table("team_members"):
        cols = _existing_columns(bind, "team_members")
        if "status" not in cols:
            op.add_column(
                "team_members",
                sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            )
        if "invited_email" not in cols:
            op.add_column("team_members", sa.Column("invited_email", sa.String(), nullable=True))
            op.create_index(
                op.f("ix_team_members_invited_email"), "team_members", ["invited_email"]
            )
        if "accepted_at" not in cols:
            op.add_column("team_members", sa.Column("accepted_at", sa.DateTime(), nullable=True))
        if "revoked_at" not in cols:
            op.add_column("team_members", sa.Column("revoked_at", sa.DateTime(), nullable=True))
        # A fresh invite has no User row yet until accepted.
        op.alter_column("team_members", "user_id", existing_type=sa.String(), nullable=True)
    else:
        # No environment observed without this table, but create it fresh
        # (matching the full current model) rather than assume — safer
        # than silently no-op-ing on a DB this migration has never seen.
        op.create_table(
            "team_members",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("invited_email", sa.String(), nullable=True),
            sa.Column("role", sa.String(), server_default="viewer"),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("invited_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index(op.f("ix_team_members_owner_id"), "team_members", ["owner_id"])
        op.create_index(op.f("ix_team_members_user_id"), "team_members", ["user_id"])
        op.create_index(op.f("ix_team_members_invited_email"), "team_members", ["invited_email"])

    # ── casts: approval workflow ──
    cast_cols = _existing_columns(bind, "casts")
    if "approval_status" not in cast_cols:
        op.add_column(
            "casts",
            sa.Column(
                "approval_status", sa.String(20), nullable=False, server_default="draft"
            ),
        )
        op.create_index(op.f("ix_casts_approval_status"), "casts", ["approval_status"])
        op.create_index(
            "ix_casts_user_id_approval_status", "casts", ["user_id", "approval_status"]
        )
    if "submitted_for_review_at" not in cast_cols:
        op.add_column("casts", sa.Column("submitted_for_review_at", sa.DateTime(), nullable=True))
    if "submitted_by" not in cast_cols:
        op.add_column(
            "casts", sa.Column("submitted_by", sa.String(), sa.ForeignKey("users.id"), nullable=True)
        )
    if "approved_at" not in cast_cols:
        op.add_column("casts", sa.Column("approved_at", sa.DateTime(), nullable=True))
    if "approved_by" not in cast_cols:
        op.add_column(
            "casts", sa.Column("approved_by", sa.String(), sa.ForeignKey("users.id"), nullable=True)
        )

    # Backfill: casts already past the point of being published shouldn't
    # retroactively look unapproved in the new review queue.
    op.execute(
        """
        UPDATE casts SET approval_status = 'approved'
        WHERE status IN ('READY', 'SCHEDULED', 'LIVE', 'COMPLETED')
          AND approval_status = 'draft'
        """
    )

    # ── users: workspace switcher hint ──
    user_cols = _existing_columns(bind, "users")
    if "last_workspace_id" not in user_cols:
        op.add_column("users", sa.Column("last_workspace_id", sa.String(), nullable=True))


def downgrade() -> None:
    user_cols = _existing_columns(op.get_bind(), "users")
    if "last_workspace_id" in user_cols:
        op.drop_column("users", "last_workspace_id")

    cast_cols = _existing_columns(op.get_bind(), "casts")
    if "approved_by" in cast_cols:
        op.drop_column("casts", "approved_by")
    if "approved_at" in cast_cols:
        op.drop_column("casts", "approved_at")
    if "submitted_by" in cast_cols:
        op.drop_column("casts", "submitted_by")
    if "submitted_for_review_at" in cast_cols:
        op.drop_column("casts", "submitted_for_review_at")
    if "approval_status" in cast_cols:
        op.drop_index("ix_casts_user_id_approval_status", "casts")
        op.drop_index(op.f("ix_casts_approval_status"), "casts")
        op.drop_column("casts", "approval_status")

    tm_cols = _existing_columns(op.get_bind(), "team_members")
    if "revoked_at" in tm_cols:
        op.drop_column("team_members", "revoked_at")
    if "accepted_at" in tm_cols:
        op.drop_column("team_members", "accepted_at")
    if "invited_email" in tm_cols:
        op.drop_index(op.f("ix_team_members_invited_email"), "team_members")
        op.drop_column("team_members", "invited_email")
    if "status" in tm_cols:
        op.drop_column("team_members", "status")
