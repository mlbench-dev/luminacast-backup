"""Add runpod_job_id column to variants table

Revision ID: f7a8b9c0d1e2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-03

Webhook architecture: track which RunPod job each variant is waiting on.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('variants', sa.Column('runpod_job_id', sa.String(), nullable=True))
    op.create_index('ix_variants_runpod_job_id', 'variants', ['runpod_job_id'])


def downgrade() -> None:
    op.drop_index('ix_variants_runpod_job_id', table_name='variants')
    op.drop_column('variants', 'runpod_job_id')
