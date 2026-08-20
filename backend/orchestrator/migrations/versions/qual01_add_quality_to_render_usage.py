"""add quality/quality_multiplier to render_usage_records

Revision ID: qual01_add_quality_to_render_usage
Revises: imp01_add_avatar_imperfections
Create Date: 2026-08-20

Cast.quality (simple/hd/hd_plus) already drives real render resolution but
had zero effect on real billing — only production_level did. This adds a
SECOND, independent multiplier (quality) alongside the existing
production_level multiplier on render_usage_records, so the audit trail can
show both. Nullable since historical rows predate this column and are not
backfilled. Guarded with a column-existence check, matching the repo's
convention for ALTERs of long-lived tables (see prof01_add_user_avatar.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "qual01_add_quality_to_render_usage"
down_revision: Union[str, None] = "imp01_add_avatar_imperfections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("render_usage_records")}
    if "quality" not in cols:
        op.add_column("render_usage_records", sa.Column("quality", sa.String(length=20), nullable=True))
    if "quality_multiplier" not in cols:
        op.add_column("render_usage_records", sa.Column("quality_multiplier", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("render_usage_records")}
    if "quality_multiplier" in cols:
        op.drop_column("render_usage_records", "quality_multiplier")
    if "quality" in cols:
        op.drop_column("render_usage_records", "quality")
