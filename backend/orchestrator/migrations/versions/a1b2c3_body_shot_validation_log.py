"""add body_shot_validation_log table

Revision ID: 067cae3e25c1
Revises: z7a8b9c0d1e2
Create Date: 2026-04-14 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "067cae3e25c1"
down_revision: Union[str, None] = "z7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "body_shot_validation_log",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("avatar_id", sa.String(40), sa.ForeignKey("avatars.id"), index=True, nullable=False),
        sa.Column("set_id", sa.String(40), nullable=False, index=True),
        sa.Column("angle", sa.String(32), nullable=False),
        sa.Column("engine_used", sa.String(50), nullable=True),
        sa.Column("attempt_number", sa.Integer, default=1),
        sa.Column("angle_match", sa.Boolean, nullable=True),
        sa.Column("angle_predicted", sa.String(32), nullable=True),
        sa.Column("angle_confidence", sa.Float, nullable=True),
        sa.Column("identity_similarity", sa.Float, nullable=True),
        sa.Column("passed", sa.Boolean, nullable=True),
        sa.Column("generation_time_ms", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("body_shot_validation_log")
