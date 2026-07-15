"""
WebSocket Event Type Definitions.
Defines all server→client and client→server event types.
"""
from enum import Enum
from pydantic import BaseModel
from typing import Optional, List


# Server → Client event types
class ServerEventType(str, Enum):
    STREAM_STATE_UPDATE = "STREAM_STATE_UPDATE"
    BLOCK_TRANSITION = "BLOCK_TRANSITION"
    NEW_CHAT_MESSAGE = "NEW_CHAT_MESSAGE"
    CHAT_DRAFT_LOCKED = "CHAT_DRAFT_LOCKED"
    AFK_TIMER_TICK = "AFK_TIMER_TICK"
    AFK_TRIGGERED = "AFK_TRIGGERED"
    OPERATOR_PRESENCE = "OPERATOR_PRESENCE"
    GENERATION_PROGRESS = "GENERATION_PROGRESS"
    AVATAR_STATUS_UPDATE = "AVATAR_STATUS_UPDATE"


# Client → Server event types
class ClientEventType(str, Enum):
    PIN_CONFIRM = "PIN_CONFIRM"
    APPROVE_CHAT = "APPROVE_CHAT"
    REJECT_CHAT = "REJECT_CHAT"
    LOCK_CHAT_DRAFT = "LOCK_CHAT_DRAFT"
    SEND_CHAT = "SEND_CHAT"
    STREAM_CONTROL = "STREAM_CONTROL"


# Server → Client payloads
class StreamStatePayload(BaseModel):
    status: str  # live | paused | idle
    uptime_seconds: float
    viewers: int
    total_purchases: int
    total_gmv: float


class BlockTransitionPayload(BaseModel):
    current_block_id: str
    current_block_position: int
    total_blocks: int
    block_type: str
    layout_mode: Optional[str] = None
    product_to_pin: Optional[dict] = None
    afk_timer_seconds: Optional[int] = None
    next_block_preview: Optional[dict] = None


class NewChatMessagePayload(BaseModel):
    message_id: str
    viewer_username: str
    message_text: str
    is_purchase: bool
    ai_draft: Optional[str] = None
    ai_draft_status: Optional[str] = None


class ChatDraftLockedPayload(BaseModel):
    message_id: str
    locked_by: str


class AFKTimerTickPayload(BaseModel):
    seconds_remaining: int
    product_id: str
    product_name: str


class AFKTriggeredPayload(BaseModel):
    product_id: str
    overlay_active: bool = True
    chat_cta_sent: bool = True


class OperatorPresencePayload(BaseModel):
    operators: List[dict]


class GenerationProgressPayload(BaseModel):
    cast_id: str
    progress: float
    current_step: Optional[str] = None
    failed_count: int = 0


# Client → Server payloads
class PinConfirmPayload(BaseModel):
    product_id: str
    operator_name: Optional[str] = None


class ApproveChatPayload(BaseModel):
    message_id: str
    edited_text: Optional[str] = None


class RejectChatPayload(BaseModel):
    message_id: str


class LockChatDraftPayload(BaseModel):
    message_id: str


class SendChatPayload(BaseModel):
    text: str
    mode: str = "chat_only"  # chat_only | voice_and_chat


class StreamControlPayload(BaseModel):
    action: str  # skip | previous | pause | resume
