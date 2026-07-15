import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True)  # prefix: msg_
    session_id = Column(String, ForeignKey("stream_sessions.id"), index=True)
    viewer_username = Column(String, nullable=False, index=True)
    message_text = Column(Text, nullable=False)
    is_purchase = Column(Boolean, default=False)
    product_id = Column(String, nullable=True)
    ai_draft = Column(Text, nullable=True)
    ai_draft_status = Column(String, default="pending")  # pending | approved | rejected | auto_sent
    responded_by = Column(String, nullable=True)
    response_text = Column(Text, nullable=True)
    locked_by = Column(String, nullable=True)
    timestamp = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    session = relationship("StreamSession", back_populates="chat_messages")
