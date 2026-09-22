"""Add casts.auto_cast (records the Auto Cast toggle state at creation time)

Revision ID: fc10_cast_auto_cast
Revises: fc09_drop_debug_face_ref
Create Date: 2026-09-22

The Auto Cast toggle previously wasn't persisted anywhere — SetupPhase
guessed its state on reload by checking production_level, which is an
unrelated setting and often guessed wrong (a fresh Auto Cast cast could
come back showing the toggle off). This column stores the real value once,
at creation, so it can be read back directly instead of inferred.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc10_cast_auto_cast"
down_revision: Union[str, None] = "fc09_drop_debug_face_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "auto_cast" not in columns:
        op.add_column(
            "casts",
            sa.Column("auto_cast", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("casts")}

    if "auto_cast" in columns:
        op.drop_column("casts", "auto_cast")
