"""Admin cost dashboard endpoints.

Three read-only routes for the operator (3gorka72@gmail.com) to monitor
platform economics. Guarded by `utils.admin.require_admin` (email
allow-list, separate from the role-based admin used elsewhere).

Mounted under `/api/admin/costs`. Depends on the foundation tables
(`UsageEvent`) populated by PR β instrumentation.
"""
from datetime import date, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.usage import UsageEvent
from models.user import User
from services.cost_rates import COST_RATES, MARKUP_MULTIPLIER
from utils.admin import require_admin


router = APIRouter(prefix="/api/admin/costs", tags=["admin-costs"])


@router.get("/overview")
async def cost_overview(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Platform economics overview for the admin dashboard."""
    try:
        since = date.today() - timedelta(days=days)

        totals = await db.execute(
            select(
                func.sum(UsageEvent.provider_cost_usd).label("total_provider_cost"),
                func.sum(UsageEvent.user_price_usd).label("total_user_price"),
                func.count(UsageEvent.id).label("total_events"),
            ).where(UsageEvent.created_at >= since)
        )
        row = totals.first()

        # HOSTKEY is decommissioned (PR #94). Its historical USAGE rows
        # (provider='hostkey', $0.00) keep accumulating from a leftover
        # usage log line, cluttering the Cost-by-Provider table with a
        # dead provider. Exclude it here so the breakdown only shows live
        # providers.
        by_provider_rows = (
            await db.execute(
                select(
                    UsageEvent.provider,
                    func.sum(UsageEvent.provider_cost_usd).label("cost"),
                    func.count(UsageEvent.id).label("count"),
                )
                .where(UsageEvent.created_at >= since)
                .where(UsageEvent.provider != "hostkey")
                .group_by(UsageEvent.provider)
                .order_by(func.sum(UsageEvent.provider_cost_usd).desc())
            )
        ).all()

        by_type_rows = (
            await db.execute(
                select(
                    UsageEvent.event_type,
                    func.sum(UsageEvent.provider_cost_usd).label("cost"),
                    func.count(UsageEvent.id).label("count"),
                    func.avg(UsageEvent.provider_cost_usd).label("avg_cost"),
                )
                .where(UsageEvent.created_at >= since)
                .group_by(UsageEvent.event_type)
                .order_by(func.sum(UsageEvent.provider_cost_usd).desc())
            )
        ).all()

        # LEFT JOIN (not inner) so a usage row from a since-deleted user still
        # shows up with its raw id instead of silently vanishing from the
        # total — this table is about cost accounting, not user management.
        by_user_rows = (
            await db.execute(
                select(
                    UsageEvent.user_id,
                    User.email,
                    User.display_name,
                    func.sum(UsageEvent.provider_cost_usd).label("cost"),
                    func.sum(UsageEvent.user_price_usd).label("revenue"),
                    func.count(UsageEvent.id).label("events"),
                )
                .outerjoin(User, User.id == UsageEvent.user_id)
                .where(UsageEvent.created_at >= since)
                .group_by(UsageEvent.user_id, User.email, User.display_name)
                .order_by(func.sum(UsageEvent.provider_cost_usd).desc())
            )
        ).all()

        daily_rows = (
            await db.execute(
                select(
                    func.date(UsageEvent.created_at).label("day"),
                    func.sum(UsageEvent.provider_cost_usd).label("cost"),
                    func.sum(UsageEvent.user_price_usd).label("revenue"),
                    func.count(UsageEvent.id).label("events"),
                )
                .where(UsageEvent.created_at >= since)
                .group_by(func.date(UsageEvent.created_at))
                .order_by(func.date(UsageEvent.created_at))
            )
        ).all()

        total_cost = float(row.total_provider_cost or 0)
        total_revenue = float(row.total_user_price or 0)
        fixed_monthly = (
            COST_RATES["fixed/vps_hostinger"]
            + COST_RATES["fixed/mubert_plan"]
        ) * (days / 30)

        gross_margin_pct = (
            round((total_revenue - total_cost) / total_revenue * 100, 1)
            if total_revenue > 0
            else 0
        )

        return {
            "period_days": days,
            "totals": {
                "provider_cost": round(total_cost, 2),
                "user_revenue": round(total_revenue, 2),
                "gross_margin": round(total_revenue - total_cost, 2),
                "gross_margin_pct": gross_margin_pct,
                "fixed_costs": round(fixed_monthly, 2),
                "net_margin": round(total_revenue - total_cost - fixed_monthly, 2),
                "total_events": row.total_events or 0,
            },
            "by_provider": [
                {
                    "provider": r.provider,
                    "cost": round(float(r.cost or 0), 2),
                    "count": r.count,
                }
                for r in by_provider_rows
            ],
            "by_event_type": [
                {
                    "type": r.event_type,
                    "cost": round(float(r.cost or 0), 2),
                    "count": r.count,
                    "avg": round(float(r.avg_cost or 0), 4),
                }
                for r in by_type_rows
            ],
            "by_user": [
                {
                    "user_id": r.user_id,
                    "email": r.email,
                    "display_name": r.display_name,
                    "cost": round(float(r.cost or 0), 2),
                    "revenue": round(float(r.revenue or 0), 2),
                    "events": r.events,
                }
                for r in by_user_rows
            ],
            "daily_trend": [
                {
                    "date": str(r.day),
                    "cost": round(float(r.cost or 0), 2),
                    "revenue": round(float(r.revenue or 0), 2),
                    "events": r.events,
                }
                for r in daily_rows
            ],
            "markup_multiplier": MARKUP_MULTIPLIER,
        }
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise


@router.get("/cast/{cast_id}")
async def cast_cost_breakdown(
    cast_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-event cost breakdown for a specific cast."""
    try:
        result = await db.execute(
            select(UsageEvent)
            .where(UsageEvent.resource_id == cast_id)
            .order_by(UsageEvent.created_at)
        )
        # Spec materialises the iterator twice — `events.scalars()` returns a
        # consume-once iterator, so we resolve it to a list before summing.
        rows = result.scalars().all()

        return {
            "cast_id": cast_id,
            "events": [
                {
                    "type": e.event_type,
                    "provider": e.provider,
                    "cost": round(float(e.provider_cost_usd or 0), 4),
                    "price": round(float(e.user_price_usd or 0), 4),
                    "quantity": e.quantity,
                    "unit": e.quantity_unit,
                    "block_id": e.block_id,
                    "render_id": e.render_id,
                    "duration_s": e.duration_seconds,
                    "model": e.provider_model,
                    "created_at": str(e.created_at),
                }
                for e in rows
            ],
            "total_cost": round(sum(float(e.provider_cost_usd or 0) for e in rows), 2),
            "total_price": round(sum(float(e.user_price_usd or 0) for e in rows), 2),
        }
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise


@router.get("/rates")
async def get_current_rates(user: User = Depends(require_admin)):
    """Return the current `COST_RATES` table and markup multiplier.

    Useful for the dashboard's "Rates reference" panel and for verifying
    a deploy picked up a pricing change.
    """
    return {"rates": COST_RATES, "markup": MARKUP_MULTIPLIER}
