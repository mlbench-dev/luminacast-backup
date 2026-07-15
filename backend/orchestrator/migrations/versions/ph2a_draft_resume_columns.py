"""add DRAFT to avatar status enum + wizard_step column

Revision ID: ph2a_draft_resume
Revises: 0eacovpyb3e5
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ph2a_draft_resume"
down_revision: Union[str, None] = "0eacovpyb3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add DRAFT to avatarstatus enum (PostgreSQL)
    op.execute("ALTER TYPE avatarstatus ADD VALUE IF NOT EXISTS 'DRAFT'")

    # Add wizard_step column for tracking clone/AI flow step
    op.add_column("avatars", sa.Column("wizard_step", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("avatars", "wizard_step")
    # Note: PostgreSQL does not support removing enum values directly.
    # DRAFT value remains in the enum but is unused after downgrade.
