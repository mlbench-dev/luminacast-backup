from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    viewer_username: str
    message_text: str
    is_purchase: bool
    ai_draft: Optional[str] = None
    ai_draft_status: str
    responded_by: Optional[str] = None
    response_text: Optional[str] = None
    locked_by: Optional[str] = None
    timestamp: datetime

    model_config = {"from_attributes": True}


class ChatDraftApproval(BaseModel):
    edited_text: Optional[str] = None  # null = approve as-is


class ChatSendRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    mode: str = "chat_only"  # chat_only | voice_and_chat
    session_id: str
