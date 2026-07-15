"""indexes_columns_new_tables

Revision ID: 88c51d3c92fd
Revises: de13afbeb27c
Create Date: 2026-03-29 19:26:21.063230

B-065: Add indexes to all FK columns
B-066: Alembic migration infrastructure
B-067: Add updated_at/deleted_at columns
B-068: candidate_frames stores R2 keys (backfill separate)
B-069: Remove stream_sessions UniqueConstraint
B-070: New api_usage_logs table
B-071: New voice_models table
B-072: New scraping_jobs table
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '88c51d3c92fd'
down_revision: Union[str, None] = 'de13afbeb27c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── B-065: Add indexes to all FK columns ──

    # avatars
    op.create_index(op.f('ix_avatars_user_id'), 'avatars', ['user_id'])
    op.create_index(op.f('ix_avatars_status'), 'avatars', ['status'])

    # casts
    op.create_index(op.f('ix_casts_user_id'), 'casts', ['user_id'])
    op.create_index(op.f('ix_casts_avatar_id'), 'casts', ['avatar_id'])
    op.create_index(op.f('ix_casts_status'), 'casts', ['status'])

    # cast_products
    op.create_index(op.f('ix_cast_products_cast_id'), 'cast_products', ['cast_id'])
    op.create_index(op.f('ix_cast_products_product_id'), 'cast_products', ['product_id'])

    # blocks
    op.create_index(op.f('ix_blocks_cast_id'), 'blocks', ['cast_id'])
    op.create_index(op.f('ix_blocks_product_id'), 'blocks', ['product_id'])

    # variants
    op.create_index(op.f('ix_variants_block_id'), 'variants', ['block_id'])

    # products
    op.create_index(op.f('ix_products_user_id'), 'products', ['user_id'])

    # stream_sessions
    op.create_index(op.f('ix_stream_sessions_cast_id'), 'stream_sessions', ['cast_id'])
    op.create_index(op.f('ix_stream_sessions_user_id'), 'stream_sessions', ['user_id'])

    # chat_messages
    op.create_index(op.f('ix_chat_messages_session_id'), 'chat_messages', ['session_id'])
    op.create_index(op.f('ix_chat_messages_viewer_username'), 'chat_messages', ['viewer_username'])

    # billing_events
    op.create_index(op.f('ix_billing_events_user_id'), 'billing_events', ['user_id'])
    op.create_index(op.f('ix_billing_events_type'), 'billing_events', ['type'])

    # team_members
    op.create_index(op.f('ix_team_members_owner_id'), 'team_members', ['owner_id'])
    op.create_index(op.f('ix_team_members_user_id'), 'team_members', ['user_id'])

    # ── B-067: Add updated_at / deleted_at columns ──

    # updated_at on all tables
    op.add_column('users', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('avatars', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('casts', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('cast_products', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('products', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('blocks', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('variants', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('stream_sessions', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('chat_messages', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('billing_events', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('team_members', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # deleted_at on tables where historical data matters
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('avatars', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('casts', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('products', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('billing_events', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('stream_sessions', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # ── New columns on existing tables ──

    # users
    op.add_column('users', sa.Column('display_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(), nullable=True))

    # avatars
    op.add_column('avatars', sa.Column('source_platform', sa.String(), nullable=True))

    # billing_events
    op.add_column('billing_events', sa.Column('status', sa.String(), server_default='completed', nullable=True))
    op.add_column('billing_events', sa.Column('refund_id', sa.String(), nullable=True))

    # ── B-069: Remove stream_sessions UniqueConstraint ──
    op.drop_constraint('uq_one_active_session_per_cast', 'stream_sessions', type_='unique')

    # ── B-070: New api_usage_logs table ──
    op.create_table(
        'api_usage_logs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('avatar_id', sa.String(), sa.ForeignKey('avatars.id'), nullable=True),
        sa.Column('cast_id', sa.String(), sa.ForeignKey('casts.id'), nullable=True),
        sa.Column('service', sa.String(), nullable=False),
        sa.Column('operation', sa.String(), nullable=False),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('input_size_bytes', sa.Integer(), nullable=True),
        sa.Column('output_size_bytes', sa.Integer(), nullable=True),
        sa.Column('cost_cents', sa.Integer(), server_default='0'),
        sa.Column('runpod_job_id', sa.String(), nullable=True),
        sa.Column('success', sa.Boolean(), server_default='true'),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
    )
    op.create_index(op.f('ix_api_usage_logs_user_id'), 'api_usage_logs', ['user_id'])
    op.create_index(op.f('ix_api_usage_logs_avatar_id'), 'api_usage_logs', ['avatar_id'])
    op.create_index(op.f('ix_api_usage_logs_cast_id'), 'api_usage_logs', ['cast_id'])
    op.create_index(op.f('ix_api_usage_logs_service'), 'api_usage_logs', ['service'])

    # ── B-071: New voice_models table ──
    op.create_table(
        'voice_models',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('avatar_id', sa.String(), sa.ForeignKey('avatars.id'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('r2_model_key', sa.String(), nullable=False),
        sa.Column('source_audio_key', sa.String(), nullable=True),
        sa.Column('source_platform', sa.String(), nullable=True),
        sa.Column('duration_trained_seconds', sa.Float(), nullable=True),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index(op.f('ix_voice_models_user_id'), 'voice_models', ['user_id'])
    op.create_index(op.f('ix_voice_models_avatar_id'), 'voice_models', ['avatar_id'])

    # ── B-072: New scraping_jobs table ──
    op.create_table(
        'scraping_jobs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('handle', sa.String(), nullable=False),
        sa.Column('normalized_url', sa.String(), nullable=True),
        sa.Column('status', sa.String(), server_default='completed'),
        sa.Column('video_count', sa.Integer(), server_default='0'),
        sa.Column('result_data', sa.JSON(), nullable=True),
        sa.Column('apify_run_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
    )
    op.create_index(op.f('ix_scraping_jobs_user_id'), 'scraping_jobs', ['user_id'])
    op.create_index(op.f('ix_scraping_jobs_platform'), 'scraping_jobs', ['platform'])


def downgrade() -> None:
    # ── Drop new tables ──
    op.drop_table('scraping_jobs')
    op.drop_table('voice_models')
    op.drop_table('api_usage_logs')

    # ── Restore stream_sessions UniqueConstraint ──
    op.create_unique_constraint('uq_one_active_session_per_cast', 'stream_sessions', ['cast_id'])

    # ── Drop new columns ──
    op.drop_column('billing_events', 'refund_id')
    op.drop_column('billing_events', 'status')
    op.drop_column('avatars', 'source_platform')
    op.drop_column('users', 'last_login_at')
    op.drop_column('users', 'display_name')

    # ── Drop deleted_at columns ──
    op.drop_column('stream_sessions', 'deleted_at')
    op.drop_column('billing_events', 'deleted_at')
    op.drop_column('products', 'deleted_at')
    op.drop_column('casts', 'deleted_at')
    op.drop_column('avatars', 'deleted_at')
    op.drop_column('users', 'deleted_at')

    # ── Drop updated_at columns ──
    op.drop_column('team_members', 'updated_at')
    op.drop_column('billing_events', 'updated_at')
    op.drop_column('chat_messages', 'updated_at')
    op.drop_column('stream_sessions', 'updated_at')
    op.drop_column('variants', 'updated_at')
    op.drop_column('blocks', 'updated_at')
    op.drop_column('products', 'updated_at')
    op.drop_column('cast_products', 'updated_at')
    op.drop_column('casts', 'updated_at')
    op.drop_column('avatars', 'updated_at')
    op.drop_column('users', 'updated_at')

    # ── Drop indexes ──
    op.drop_index(op.f('ix_team_members_user_id'), 'team_members')
    op.drop_index(op.f('ix_team_members_owner_id'), 'team_members')
    op.drop_index(op.f('ix_billing_events_type'), 'billing_events')
    op.drop_index(op.f('ix_billing_events_user_id'), 'billing_events')
    op.drop_index(op.f('ix_chat_messages_viewer_username'), 'chat_messages')
    op.drop_index(op.f('ix_chat_messages_session_id'), 'chat_messages')
    op.drop_index(op.f('ix_stream_sessions_user_id'), 'stream_sessions')
    op.drop_index(op.f('ix_stream_sessions_cast_id'), 'stream_sessions')
    op.drop_index(op.f('ix_products_user_id'), 'products')
    op.drop_index(op.f('ix_variants_block_id'), 'variants')
    op.drop_index(op.f('ix_blocks_product_id'), 'blocks')
    op.drop_index(op.f('ix_blocks_cast_id'), 'blocks')
    op.drop_index(op.f('ix_cast_products_product_id'), 'cast_products')
    op.drop_index(op.f('ix_cast_products_cast_id'), 'cast_products')
    op.drop_index(op.f('ix_casts_status'), 'casts')
    op.drop_index(op.f('ix_casts_avatar_id'), 'casts')
    op.drop_index(op.f('ix_casts_user_id'), 'casts')
    op.drop_index(op.f('ix_avatars_status'), 'avatars')
    op.drop_index(op.f('ix_avatars_user_id'), 'avatars')
