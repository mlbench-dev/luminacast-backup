"""unify all open migration heads — 2026-05-05 deploy

Revision ID: m26_05_05_unify
Revises: c2026_05_04_blkmeta, cm01_commission_fields, ct01_usage_events, pa01_product_asset_position, pip01_rename_pip_th, uaf01_user_affiliates
Create Date: 2026-05-05

Today's deploy merged 19 PRs to main, several of which added parallel migrations
that all branched off `ph56_ci_wt` / `d3d540cda4fa` / `vr01_version_render_cols`
/ `e6f7a8b9c0d1` without a unifying merge node. Alembic refuses to upgrade with
multiple heads. This empty merge migration unifies them.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "m26_05_05_unify"
down_revision: Union[str, Sequence[str], None] = (
    "c2026_05_04_blkmeta",
    "cm01_commission_fields",
    "ct01_usage_events",
    "pa01_product_asset_position",
    "pip01_rename_pip_th",
    "uaf01_user_affiliates",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op; this migration only unifies the revision graph.
    pass


def downgrade() -> None:
    # No-op.
    pass
