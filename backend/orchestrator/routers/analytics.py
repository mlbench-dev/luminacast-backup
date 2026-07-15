from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database import get_db
from models.user import User
from models.stream_session import StreamSession
from models.variant import Variant
from models.block import Block
from schemas.analytics import (
    SessionAnalyticsResponse, SessionSummary,
    VariantPerformanceResponse, VariantPerformanceItem,
)
from routers.auth import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/sessions", response_model=SessionAnalyticsResponse)
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StreamSession)
        .where(StreamSession.user_id == user.id)
        .order_by(StreamSession.started_at.desc())
    )
    sessions = result.scalars().all()
    return SessionAnalyticsResponse(
        sessions=[SessionSummary.model_validate(s) for s in sessions],
        total=len(sessions),
    )


@router.get("/sessions/{session_id}", response_model=SessionSummary)
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(StreamSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/variants", response_model=VariantPerformanceResponse)
async def get_variant_performance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from models.cast import Cast
    result = await db.execute(
        select(Variant)
        .join(Block)
        .join(Cast, Block.cast_id == Cast.id)
        .where(Cast.user_id == user.id, Variant.times_played > 0)
        .options(selectinload(Variant.block))
        .order_by(Variant.performance_score.desc().nullslast())
    )
    variants = result.scalars().all()

    items = []
    for v in variants:
        items.append(VariantPerformanceItem(
            variant_id=v.id,
            block_id=v.block_id,
            block_type=v.block.type.value if v.block else "unknown",
            times_played=v.times_played,
            purchases_during=v.purchases_during,
            performance_score=v.performance_score,
            script_preview=v.script_text[:100] if v.script_text else None,
        ))

    return VariantPerformanceResponse(variants=items, total=len(items))
