from database import Base
from sqlalchemy import Column, String, Integer, Text, DateTime, Index
from sqlalchemy.sql import func


class AiPromptVersion(Base):
    __tablename__ = "ai_prompt_versions"
    id = Column(String, primary_key=True)
    prompt_name = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    prompt_text = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=True)
    changed_by = Column(String, nullable=True)
    change_reason = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_prompt_versions_name", "prompt_name", version.desc()),
    )
