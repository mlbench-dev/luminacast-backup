"""Add ai_prompt_versions table for prompt version history

Revision ID: i0a1b2c3d4e5
Revises: h9d1e2f3a4b5
Create Date: 2026-04-05

Stores historical versions of AI prompts edited via the admin control panel.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'i0a1b2c3d4e5'
down_revision: Union[str, None] = 'h9d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_prompt_versions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('prompt_name', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('prompt_text', sa.Text(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('changed_by', sa.String(), nullable=True),
        sa.Column('change_reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('idx_prompt_versions_name', 'ai_prompt_versions', ['prompt_name', sa.text('version DESC')])


def downgrade() -> None:
    op.drop_index('idx_prompt_versions_name', table_name='ai_prompt_versions')
    op.drop_table('ai_prompt_versions')
