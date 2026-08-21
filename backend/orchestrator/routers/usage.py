"""User-facing billing summary.

Returns the authenticated user's own usage for the last `days` days,
grouped by event type, with friendly aggregates for the /billing UI.
This is NOT admin-gated — every user sees their own data.
"""
from datetime import date, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.usage import UsageEvent
from models.user import User
from routers.auth import get_current_user


router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/summary")
async def my_usage_summary(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's spend grouped by event type.

    Shape matches the /billing page contract: per-category totals plus a
    raw `by_type` map for any future detail rendering.
    """
    try:
        since = date.today() - timedelta(days=days)

        rows = (
            await db.execute(
                select(
                    UsageEvent.event_type,
                    func.sum(UsageEvent.user_price_usd).label("price"),
                    func.count(UsageEvent.id).label("count"),
                )
                .where(
                    UsageEvent.user_id == user.id,
                    UsageEvent.created_at >= since,
                )
                .group_by(UsageEvent.event_type)
            )
        ).all()

        by_type: dict[str, dict[str, float]] = {
            r.event_type: {
                "price": round(float(r.price or 0), 2),
                "count": r.count,
            }
            for r in rows
        }

        total_price = round(sum(v["price"] for v in by_type.values()), 2)

        # Render encompasses four event types — sum them so the /billing
        # "Renders" / "Video creation" rows show one combined figure.
        # action_render was missing here (block-render costs for
        # action/b-roll blocks silently vanished from this row, though
        # still counted in total_price below — the two numbers not
        # matching was the reported symptom).
        render_types = ("avatar_render", "motion_render", "pip_render", "action_render")
        render_count = sum(by_type.get(t, {}).get("count", 0) for t in render_types)
        render_price = round(
            sum(by_type.get(t, {}).get("price", 0) for t in render_types), 2
        )

        return {
            "period_days": days,
            "total_price": total_price,
            "render_count": render_count,
            "render_price": render_price,
            "music_price": by_type.get("music_generation", {}).get("price", 0),
            "body_shot_price": by_type.get("body_shot_generation", {}).get("price", 0),
            "publish_price": by_type.get("social_publish", {}).get("price", 0),
            "by_type": by_type,
        }
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise
