"""add change_summary to cast_versions and render metadata to cast_renders

Revision ID: vr01_version_render_cols
Revises: p24_cast_format_family
Create Date: 2026-04-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "vr01_version_render_cols"
down_revision: Union[str, None] = "p24_cast_format_family"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # cast_versions: add change_summary
    op.add_column("cast_versions", sa.Column("change_summary", sa.String(200), nullable=True))

    # cast_renders: add version, duration_seconds, quality, is_selected, thumbnail_key
    op.add_column("cast_renders", sa.Column("version", sa.Integer, nullable=True))
    op.add_column("cast_renders", sa.Column("duration_seconds", sa.Integer, nullable=True))
    op.add_column("cast_renders", sa.Column("quality", sa.String(20), nullable=True))
    op.add_column("cast_renders", sa.Column("is_selected", sa.Integer, nullable=False, server_default="0"))
    op.add_column("cast_renders", sa.Column("thumbnail_key", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("cast_renders", "thumbnail_key")
    op.drop_column("cast_renders", "is_selected")
    op.drop_column("cast_renders", "quality")
    op.drop_column("cast_renders", "duration_seconds")
    op.drop_column("cast_renders", "version")
    op.drop_column("cast_versions", "change_summary")
