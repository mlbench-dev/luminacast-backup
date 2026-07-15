import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class BillingEventType(str, enum.Enum):
    AVATAR_SETUP = "avatar_setup"
    CAST_CREATION = "cast_creation"
    STREAMING_USAGE = "streaming_usage"
    CLIP_REGENERATION = "clip_regeneration"


class BillingEvent(Base):
    __tablename__ = "billing_events"
    id = Column(String, primary_key=True)  # prefix: bill_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    type = Column(Enum(BillingEventType), index=True)
    amount_cents = Column(Integer)
    stripe_payment_intent_id = Column(String, nullable=True)
    related_id = Column(String, nullable=True)
    status = Column(String, default="completed")  # completed | pending | failed | refunded
    refund_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
