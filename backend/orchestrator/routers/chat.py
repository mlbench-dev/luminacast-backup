import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.user import User
from models.chat_message import ChatMessage
from models.stream_session import StreamSession
from schemas.chat import ChatMessageResponse, ChatDraftApproval, ChatSendRequest
from routers.auth import get_current_user, require_creator_or_operator

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/approve/{msg_id}", response_model=ChatMessageResponse)
async def approve_draft(
    msg_id: str,
    req: ChatDraftApproval,
    user: User = Depends(require_creator_or_operator),
    db: AsyncSession = Depends(get_db),
):
    msg = await db.get(ChatMessage, msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg.locked_by and msg.locked_by != user.id:
        raise HTTPException(status_code=409, detail="Message is locked by another operator")

    msg.ai_draft_status = "approved"
    msg.responded_by = user.id
    msg.response_text = req.edited_text or msg.ai_draft
    msg.locked_by = None
    await db.commit()
    await db.refresh(msg)

    # Broadcast to session
    from websocket.manager import ws_manager
    await ws_manager.broadcast_to_session(msg.session_id, {
        "type": "CHAT_DRAFT_APPROVED",
        "payload": {"message_id": msg_id, "response_text": msg.response_text},
    })

    return msg


@router.post("/reject/{msg_id}", response_model=ChatMessageResponse)
async def reject_draft(
    msg_id: str,
    user: User = Depends(require_creator_or_operator),
    db: AsyncSession = Depends(get_db),
):
    msg = await db.get(ChatMessage, msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.ai_draft_status = "rejected"
    msg.responded_by = user.id
    msg.locked_by = None
    await db.commit()
    await db.refresh(msg)

    return msg


@router.post("/send")
async def send_message(
    req: ChatSendRequest,
    user: User = Depends(require_creator_or_operator),
    db: AsyncSession = Depends(get_db),
):
    # Create outgoing message record
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    msg = ChatMessage(
        id=msg_id,
        session_id=req.session_id,
        viewer_username=f"@{user.email.split('@')[0]}",
        message_text=req.text,
        responded_by=user.id,
        response_text=req.text,
        ai_draft_status="manual",
    )
    db.add(msg)
    await db.commit()

    # Broadcast to chat monitor for TikTok posting
    from websocket.manager import ws_manager
    await ws_manager.broadcast_to_internal({
        "type": "SEND_CHAT",
        "payload": {
            "session_id": req.session_id,
            "text": req.text,
            "mode": req.mode,
        },
    })

    return {"message_id": msg_id, "status": "sent"}


@router.post("/lock/{msg_id}")
async def lock_draft(
    msg_id: str,
    user: User = Depends(require_creator_or_operator),
    db: AsyncSession = Depends(get_db),
):
    msg = await db.get(ChatMessage, msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg.locked_by and msg.locked_by != user.id:
        raise HTTPException(status_code=409, detail="Message is locked by another operator")

    msg.locked_by = user.id
    await db.commit()

    from websocket.manager import ws_manager
    await ws_manager.broadcast_to_session(msg.session_id, {
        "type": "CHAT_DRAFT_LOCKED",
        "payload": {"message_id": msg_id, "locked_by": user.id},
    })

    return {"message_id": msg_id, "locked_by": user.id}
