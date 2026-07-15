"""User-action audit log model.

One row per user-initiated mutation (or system mutation). Append-only;
written by `services.audit_log.record()` and never updated.
"""
from database import Base
from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func


class UserActionEvent(Base):
    __tablename__ = "user_action_events"

    id = Column(String(40), primary_key=True)  # prefix: uae_
    user_id = Column(String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id = Column(String(80), nullable=True)
    action = Column(String(120), nullable=False, index=True)
    entity_type = Column(String(40), nullable=False)
    entity_id = Column(String(80), nullable=True)
    cast_id = Column(String(40), ForeignKey("casts.id", ondelete="SET NULL"), nullable=True)
    before = Column(JSONB, nullable=True)
    after = Column(JSONB, nullable=True)
    # SQLAlchemy reserves `metadata` on the Base class; map a Python attribute
    # `event_metadata` to the SQL column `metadata`.
    event_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_uae_user_created", "user_id", "created_at"),
        Index("ix_uae_cast_created", "cast_id", "created_at"),
        Index("ix_uae_entity", "entity_type", "entity_id"),
    )
