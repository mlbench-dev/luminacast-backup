"""add cast version, cast_versions table, avatar detected_language

Revision ID: a1b2c3d4e5f6
Revises: 0eacovpyb3e5
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "0eacovpyb3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cast versioning
    op.add_column("casts", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))

    # Cast versions snapshot table
    op.create_table(
        "cast_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("cast_id", sa.String(), sa.ForeignKey("casts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("blocks_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("status_at_snapshot", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # Avatar detected_language
    op.add_column("avatars", sa.Column("detected_language", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("avatars", "detected_language")
    op.drop_table("cast_versions")
    op.drop_column("casts", "version")
