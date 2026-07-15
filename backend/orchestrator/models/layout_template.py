from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, func
from database import Base


class LayoutTemplate(Base):
    __tablename__ = "layout_templates"

    id = Column(String, primary_key=True)               # prefix: lt_
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=True)
    name = Column(String, nullable=False)
    config = Column(JSON, nullable=False)
    is_preset = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
