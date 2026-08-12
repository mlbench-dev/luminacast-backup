"""unify heads (crt01 cast_render.celery_task_id + m28 teams) — 2026-08-12

Revision ID: m29_unify_heads
Revises: crt01_cast_render_task_id, m28_teams_and_approval_status
Create Date: 2026-08-12

`alembic heads` reports two open heads (crt01_cast_render_task_id and
m28_teams_and_approval_status never got reconciled). Following the
established repo pattern (m24_merge_all_heads, m26_05_05_unify_heads,
m27_unify_heads), this empty merge migration unifies them so the new
billing migration (bil01_add_billing_tables) has a single head to chain
onto.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "m29_unify_heads"
down_revision: Union[str, Sequence[str], None] = (
    "crt01_cast_render_task_id",
    "m28_teams_and_approval_status",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op; this migration only unifies the revision graph.
    pass


def downgrade() -> None:
    pass
