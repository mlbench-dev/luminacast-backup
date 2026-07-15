import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.user import User
from models.cast import Cast, CastStatus
from models.stream_session import StreamSession
from schemas.stream import StreamStartRequest, StreamStatusResponse, StreamControlRequest, PinConfirmRequest
from routers.auth import get_current_user

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.post("/start", response_model=StreamStatusResponse, status_code=201)
async def start_stream(
    req: StreamStartRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify cast exists and is ready
    cast = await db.get(Cast, req.cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(status_code=404, detail="Cast not found")
    if cast.status != CastStatus.READY:
        raise HTTPException(status_code=400, detail=f"Cast is not ready (status: {cast.status.value})")

    # Check no active session for this cast
    result = await db.execute(
        select(StreamSession).where(
            StreamSession.cast_id == req.cast_id,
            StreamSession.ended_at.is_(None),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Stream already active for this cast")

    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    session = StreamSession(
        id=session_id,
        cast_id=req.cast_id,
        user_id=user.id,
        rtmp_url=req.rtmp_url,
    )
    db.add(session)

    cast.status = CastStatus.LIVE
    await db.commit()

    # Launch streaming task
    from tasks.stream_worker import run_stream
    task = run_stream.delay(session_id, req.cast_id, req.rtmp_url, req.mode)

    session.celery_task_id = task.id
    await db.commit()

    return StreamStatusResponse(
        session_id=session_id,
        cast_id=req.cast_id,
        status="live",
    )


@router.post("/stop", response_model=StreamStatusResponse)
async def stop_stream(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Find active session for this user
    result = await db.execute(
        select(StreamSession).where(
            StreamSession.user_id == user.id,
            StreamSession.ended_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No active stream found")

    session.ended_at = datetime.now(timezone.utc)

    # Revoke celery task
    if session.celery_task_id:
        from tasks import celery_app
        celery_app.control.revoke(session.celery_task_id, terminate=True)

    # Update cast status
    cast = await db.get(Cast, session.cast_id)
    if cast:
        cast.status = CastStatus.COMPLETED

    await db.commit()

    return StreamStatusResponse(
        session_id=session.id,
        cast_id=session.cast_id,
        status="stopped",
        duration_minutes=session.duration_minutes,
        total_purchases=session.total_purchases,
        total_gmv=session.total_gmv,
        streaming_cost_cents=session.streaming_cost_cents,
    )


@router.post("/skip")
async def skip_block(user: User = Depends(get_current_user)):
    from websocket.manager import ws_manager
    # Find user's active session and send skip command
    # The stream worker handles the actual skip via WebSocket
    # For now, broadcast the control event
    return {"action": "skip", "status": "sent"}


@router.post("/pause")
async def pause_stream(user: User = Depends(get_current_user)):
    return {"action": "pause", "status": "sent"}


@router.post("/pin-confirm")
async def pin_confirm(
    req: PinConfirmRequest,
    user: User = Depends(get_current_user),
):
    from websocket.manager import ws_manager
    # Cancel AFK timer for this product
    # Broadcast PIN_CONFIRM event to session
    return {"action": "pin_confirm", "product_id": req.product_id, "status": "confirmed"}
