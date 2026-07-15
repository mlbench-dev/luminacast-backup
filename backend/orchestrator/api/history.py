"""User action audit log — read endpoints.

Three GETs:
  - GET /api/casts/{cast_id}/history    — events for a cast (owner only)
  - GET /api/users/me/history           — events for the current user
  - GET /api/admin/history              — admin-only, all events with filters

Cursor pagination on `(created_at desc, id desc)` so the client can scroll
back without sliding rows. `limit` is capped at 200.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.cast import Cast
from models.user import User
from models.user_action_event import UserActionEvent
from routers.auth import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["history"])

_MAX_LIMIT = 200


def _row_to_dict(row: UserActionEvent) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "session_id": row.session_id,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "cast_id": row.cast_id,
        "before": row.before,
        "after": row.after,
        "metadata": row.event_metadata,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _apply_cursor(stmt, before: Optional[str], db_col_created, db_col_id):
    """Cursor pagination on (created_at desc, id desc).

    `before` is "<created_at_iso>|<id>" returned from the previous page.
    """
    if not before:
        return stmt
    try:
        ts_str, last_id = before.split("|", 1)
        ts = datetime.fromisoformat(ts_str)
        return stmt.where(
            (db_col_created < ts) | ((db_col_created == ts) & (db_col_id < last_id))
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return stmt


def _next_cursor(rows: list[UserActionEvent]) -> Optional[str]:
    if not rows:
        return None
    last = rows[-1]
    if not last.created_at:
        return None
    return f"{last.created_at.isoformat()}|{last.id}"


@router.get("/casts/{cast_id}/history")
async def get_cast_history(
    cast_id: str,
    limit: int = Query(50, ge=1, le=_MAX_LIMIT),
    before: Optional[str] = Query(None, description="cursor: '<iso>|<id>' from previous page"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    stmt = (
        select(UserActionEvent)
        .where(UserActionEvent.cast_id == cast_id)
        .order_by(desc(UserActionEvent.created_at), desc(UserActionEvent.id))
        .limit(limit)
    )
    stmt = _apply_cursor(stmt, before, UserActionEvent.created_at, UserActionEvent.id)

    try:
        result = await db.execute(stmt)
        rows = result.scalars().all()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Failed to load history")

    return {
        "events": [_row_to_dict(r) for r in rows],
        "next_cursor": _next_cursor(list(rows)) if len(rows) == limit else None,
    }


@router.get("/users/me/history")
async def get_my_history(
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
    before: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(UserActionEvent)
        .where(UserActionEvent.user_id == user.id)
        .order_by(desc(UserActionEvent.created_at), desc(UserActionEvent.id))
        .limit(limit)
    )
    if action:
        stmt = stmt.where(UserActionEvent.action == action)
    if entity_type:
        stmt = stmt.where(UserActionEvent.entity_type == entity_type)
    stmt = _apply_cursor(stmt, before, UserActionEvent.created_at, UserActionEvent.id)

    try:
        result = await db.execute(stmt)
        rows = result.scalars().all()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Failed to load history")

    return {
        "events": [_row_to_dict(r) for r in rows],
        "next_cursor": _next_cursor(list(rows)) if len(rows) == limit else None,
    }


@router.get("/admin/history")
async def get_admin_history(
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
    before: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    cast_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO datetime — events at or after"),
    until: Optional[str] = Query(None, description="ISO datetime — events at or before"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(UserActionEvent)
        .order_by(desc(UserActionEvent.created_at), desc(UserActionEvent.id))
        .limit(limit)
    )
    if user_id:
        stmt = stmt.where(UserActionEvent.user_id == user_id)
    if cast_id:
        stmt = stmt.where(UserActionEvent.cast_id == cast_id)
    if action:
        stmt = stmt.where(UserActionEvent.action == action)
    if entity_type:
        stmt = stmt.where(UserActionEvent.entity_type == entity_type)
    if since:
        try:
            stmt = stmt.where(UserActionEvent.created_at >= datetime.fromisoformat(since))
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise HTTPException(400, "Invalid 'since' (ISO 8601 expected)")
    if until:
        try:
            stmt = stmt.where(UserActionEvent.created_at <= datetime.fromisoformat(until))
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise HTTPException(400, "Invalid 'until' (ISO 8601 expected)")

    stmt = _apply_cursor(stmt, before, UserActionEvent.created_at, UserActionEvent.id)

    try:
        result = await db.execute(stmt)
        rows = result.scalars().all()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Failed to load history")

    return {
        "events": [_row_to_dict(r) for r in rows],
        "next_cursor": _next_cursor(list(rows)) if len(rows) == limit else None,
    }
