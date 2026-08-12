"""add celery_task_id to cast_renders

Revision ID: crt01_cast_render_task_id
Revises: vr01_version_render_cols
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "crt01_cast_render_task_id"
down_revision: Union[str, None] = "vr01_version_render_cols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cast_renders", sa.Column("celery_task_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("cast_renders", "celery_task_id")