"""Add sound_casts and music_tracks tables

Revision ID: n5f6g7h8i9j0
Revises: m4e5f6g7h8i9
Create Date: 2026-04-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "n5f6g7h8i9j0"
down_revision: Union[str, None] = "m4e5f6g7h8i9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types (IF NOT EXISTS for idempotency)
    op.execute("DO $$ BEGIN CREATE TYPE soundcaststatus AS ENUM ('draft', 'training_pending', 'training_in_progress', 'trained', 'failed'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE TYPE musictrackstatus AS ENUM ('pending', 'generating', 'ready', 'failed'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    op.create_table(
        "sound_casts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), server_default=""),
        sa.Column("status", sa.Enum("draft", "training_pending", "training_in_progress", "trained", "failed", name="soundcaststatus"), server_default="draft", index=True),
        sa.Column("lora_r2_key", sa.String(), server_default=""),
        sa.Column("training_audio_keys", sa.JSON(), server_default="[]"),
        sa.Column("training_prompts", sa.JSON(), server_default="[]"),
        sa.Column("training_steps", sa.Integer(), server_default="2400"),
        sa.Column("training_loss", sa.Float(), server_default="0.0"),
        sa.Column("training_started_at", sa.DateTime(), nullable=True),
        sa.Column("training_completed_at", sa.DateTime(), nullable=True),
        sa.Column("training_error", sa.String(), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "music_tracks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("sound_cast_id", sa.String(), sa.ForeignKey("sound_casts.id", ondelete="CASCADE"), index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prompt", sa.String(), nullable=False),
        sa.Column("lyrics", sa.String(), server_default=""),
        sa.Column("duration_seconds", sa.Float(), server_default="60.0"),
        sa.Column("seed", sa.Integer(), server_default="-1"),
        sa.Column("guidance_scale", sa.Float(), server_default="15.0"),
        sa.Column("inference_steps", sa.Integer(), server_default="60"),
        sa.Column("scheduler_type", sa.String(), server_default="euler"),
        sa.Column("status", sa.Enum("pending", "generating", "ready", "failed", name="musictrackstatus"), server_default="pending", index=True),
        sa.Column("audio_r2_key", sa.String(), server_default=""),
        sa.Column("actual_duration_seconds", sa.Float(), server_default="0.0"),
        sa.Column("generation_time_seconds", sa.Float(), server_default="0.0"),
        sa.Column("generation_error", sa.String(), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("music_tracks")
    op.drop_table("sound_casts")
    op.execute("DROP TYPE IF EXISTS musictrackstatus")
    op.execute("DROP TYPE IF EXISTS soundcaststatus")
