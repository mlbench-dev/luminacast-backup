from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast as sa_cast, Date, case
from database import get_db
from models.user import User, UserRole
from models.stream_session import StreamSession
from models.cast import Cast, CastStatus
from models.billing_event import BillingEvent
from models.api_usage_log import ApiUsageLog
from models.ai_prompt_version import AiPromptVersion
from models.avatar import Avatar
from models.product import Product
from models.social_post import SocialChannel
from routers.auth import require_admin
from services import audit_log
import sentry_sdk
import uuid
from datetime import datetime, timezone, timedelta

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Existing endpoints ──


@router.get("/dashboard")
async def admin_dashboard(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    active_streams = await db.execute(
        select(func.count(StreamSession.id)).where(StreamSession.ended_at.is_(None))
    )
    total_creators = await db.execute(
        select(func.count(User.id)).where(User.role != UserRole.ADMIN)
    )
    today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_naive = today_start_utc.replace(tzinfo=None)
    revenue_today = await db.execute(
        select(func.coalesce(func.sum(BillingEvent.amount_cents), 0))
        .where(BillingEvent.created_at >= today_start_naive)
    )
    import psutil
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        health = {
            "status": "healthy",
            "cpu_percent": cpu,
            "memory_percent": memory.percent,
            "disk_percent": disk.percent,
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
        }
    except Exception:
        health = {"status": "healthy", "cpu_percent": 0, "memory_percent": 0, "disk_percent": 0}

    return {
        "active_streams": active_streams.scalar() or 0,
        "total_creators": total_creators.scalar() or 0,
        "revenue_today_cents": revenue_today.scalar() or 0,
        "server_health": health,
    }


@router.get("/streams")
async def admin_streams(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StreamSession)
        .where(StreamSession.ended_at.is_(None))
        .order_by(StreamSession.started_at.desc())
    )
    sessions = result.scalars().all()
    active = []
    for s in sessions:
        creator = await db.get(User, s.user_id)
        creator_email = creator.email if creator else "unknown"
        uptime = 0
        if s.started_at:
            now = datetime.now(timezone.utc)
            started = s.started_at.replace(tzinfo=timezone.utc) if s.started_at.tzinfo is None else s.started_at
            uptime = int((now - started).total_seconds())
        active.append({
            "session_id": s.id, "cast_id": s.cast_id, "user_id": s.user_id,
            "creator_email": creator_email, "status": "live",
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "uptime_seconds": uptime, "viewers": s.peak_viewers or 0,
            "total_purchases": s.total_purchases or 0, "total_gmv": s.total_gmv or 0,
        })
    return {"active_streams": active}


@router.get("/creators")
async def admin_creators(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.role != UserRole.ADMIN).order_by(User.created_at.desc())
    )
    creators = result.scalars().all()
    creator_list = []
    for c in creators:
        cast_count = (await db.execute(select(func.count(Cast.id)).where(Cast.user_id == c.id))).scalar() or 0
        stream_count = (await db.execute(select(func.count(StreamSession.id)).where(StreamSession.user_id == c.id))).scalar() or 0
        total_revenue = (await db.execute(select(func.coalesce(func.sum(BillingEvent.amount_cents), 0)).where(BillingEvent.user_id == c.id))).scalar() or 0
        # Real, OAuth-verified TikTok connections (via Zernio) rather than
        # the free-text handle collected at signup (User.tiktok_handle) —
        # that field is optional, never validated, and unrelated to whether
        # the user actually connected an account. A user can have more than
        # one TikTok account connected, so this is a list, not a single value.
        tiktok_rows = (
            await db.execute(
                select(SocialChannel.handle, SocialChannel.follower_count)
                .where(
                    SocialChannel.user_id == c.id,
                    SocialChannel.platform == "tiktok",
                    SocialChannel.status == "active",
                )
            )
        ).all()
        tiktok_accounts = [
            {"handle": r.handle, "follower_count": r.follower_count or 0} for r in tiktok_rows
        ]
        creator_list.append({
            "id": c.id, "email": c.email, "tiktok_accounts": tiktok_accounts,
            "total_casts": cast_count, "total_streams": stream_count,
            "total_revenue_cents": total_revenue,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "is_active": c.is_active,
        })
    return {"creators": creator_list}


@router.get("/health")
async def admin_health(user: User = Depends(require_admin)):
    import psutil
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "status": "healthy", "cpu_percent": cpu,
            "memory_percent": memory.percent, "disk_percent": disk.percent,
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
        }
    except Exception:
        return {"status": "healthy", "cpu_percent": 0, "memory_percent": 0, "disk_percent": 0}


# ── Prompt Management (CRUD) ──


@router.get("/prompts")
async def list_prompts(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return all AI prompts with metadata."""
    from services.ai_prompts import get_all_prompts

    prompts = get_all_prompts()

    # Get usage counts from api_usage_logs (approximate by matching service names)
    # Get version counts from ai_prompt_versions
    version_counts = {}
    try:
        vc_result = await db.execute(
            select(
                AiPromptVersion.prompt_name,
                func.max(AiPromptVersion.version).label("max_version"),
                func.max(AiPromptVersion.created_at).label("last_updated"),
            ).group_by(AiPromptVersion.prompt_name)
        )
        for row in vc_result.all():
            version_counts[row.prompt_name] = {
                "version": row.max_version,
                "last_updated": row.last_updated.isoformat() if row.last_updated else None,
            }
    except Exception:
        pass

    return {
        "prompts": [
            {
                "key": key,
                "name": p["name"],
                "description": p["description"],
                "category": p.get("category", "llm"),
                "system_prompt": p["system"],
                "character_count": len(p["system"]),
                "used_in": p.get("used_in", ""),
                "version": version_counts.get(key, {}).get("version", 0),
                "last_updated": version_counts.get(key, {}).get("last_updated"),
            }
            for key, p in prompts.items()
        ],
        "total": len(prompts),
    }


@router.get("/prompts/{name}")
async def get_prompt_detail(
    name: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get single prompt with full text + version history."""
    from services.ai_prompts import get_all_prompts

    prompts = get_all_prompts()
    if name not in prompts:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")

    p = prompts[name]

    # Get version history
    history_result = await db.execute(
        select(AiPromptVersion)
        .where(AiPromptVersion.prompt_name == name)
        .order_by(AiPromptVersion.version.desc())
        .limit(50)
    )
    history = [
        {
            "id": v.id,
            "version": v.version,
            "prompt_text": v.prompt_text,
            "system_prompt": v.system_prompt,
            "changed_by": v.changed_by,
            "change_reason": v.change_reason,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in history_result.scalars().all()
    ]

    return {
        "key": name,
        "name": p["name"],
        "description": p["description"],
        "category": p.get("category", "llm"),
        "system_prompt": p["system"],
        "character_count": len(p["system"]),
        "used_in": p.get("used_in", ""),
        "version": history[0]["version"] if history else 0,
        "history": history,
    }


class PromptUpdateRequest(BaseModel):
    system_prompt: str
    change_reason: str = ""


@router.put("/prompts/{name}")
async def update_prompt(
    name: str,
    body: PromptUpdateRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update prompt text. Saves old version to history first."""
    from services.ai_prompts import get_all_prompts, update_prompt_text

    prompts = get_all_prompts()
    if name not in prompts:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")

    old_text = prompts[name]["system"]

    # Determine next version number
    max_ver = await db.execute(
        select(func.coalesce(func.max(AiPromptVersion.version), 0))
        .where(AiPromptVersion.prompt_name == name)
    )
    next_version = (max_ver.scalar() or 0) + 1

    # Save current version to history
    version_record = AiPromptVersion(
        id=f"pv_{uuid.uuid4().hex[:12]}",
        prompt_name=name,
        version=next_version,
        prompt_text=old_text,
        system_prompt=old_text,
        changed_by=user.email,
        change_reason=body.change_reason or "Updated via admin panel",
    )
    db.add(version_record)
    await db.commit()

    # Update the live prompt
    update_prompt_text(name, body.system_prompt)

    try:
        await audit_log.record(
            db, user_id=user.id, action="admin.prompt_update", entity_type="admin",
            entity_id=name, after={"version": next_version, "change_reason": (body.change_reason or "")[:200]},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "key": name,
        "system_prompt": body.system_prompt,
        "version": next_version,
        "message": "Prompt updated successfully",
    }


@router.post("/prompts/{name}/revert/{version}")
async def revert_prompt(
    name: str,
    version: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revert a prompt to a previous version."""
    from services.ai_prompts import get_all_prompts, update_prompt_text

    prompts = get_all_prompts()
    if name not in prompts:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")

    # Find the target version
    result = await db.execute(
        select(AiPromptVersion)
        .where(AiPromptVersion.prompt_name == name, AiPromptVersion.version == version)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail=f"Version {version} not found for '{name}'")

    # Save current as new version before reverting
    old_text = prompts[name]["system"]
    max_ver = await db.execute(
        select(func.coalesce(func.max(AiPromptVersion.version), 0))
        .where(AiPromptVersion.prompt_name == name)
    )
    next_version = (max_ver.scalar() or 0) + 1

    version_record = AiPromptVersion(
        id=f"pv_{uuid.uuid4().hex[:12]}",
        prompt_name=name,
        version=next_version,
        prompt_text=old_text,
        system_prompt=old_text,
        changed_by=user.email,
        change_reason=f"Reverted to version {version}",
    )
    db.add(version_record)
    await db.commit()

    # Apply the revert
    update_prompt_text(name, target.prompt_text)

    try:
        await audit_log.record(
            db, user_id=user.id, action="admin.prompt_revert", entity_type="admin",
            entity_id=name, after={"reverted_to_version": version, "new_version": next_version},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "key": name,
        "system_prompt": target.prompt_text,
        "reverted_to_version": version,
        "new_version": next_version,
        "message": f"Reverted to version {version}",
    }


# ── Usage Tracking ──


@router.get("/usage/daily")
async def usage_daily(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=30, ge=1, le=90),
):
    """Aggregate api_usage_logs by day."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    result = await db.execute(
        select(
            sa_cast(ApiUsageLog.created_at, Date).label("day"),
            ApiUsageLog.service,
            func.count().label("total_calls"),
            func.sum(ApiUsageLog.cost_cents).label("total_cost_cents"),
            func.sum(case((ApiUsageLog.success == True, 1), else_=0)).label("success_count"),
            func.sum(case((ApiUsageLog.success == False, 1), else_=0)).label("fail_count"),
        )
        .where(ApiUsageLog.created_at >= cutoff)
        .group_by(sa_cast(ApiUsageLog.created_at, Date), ApiUsageLog.service)
        .order_by(sa_cast(ApiUsageLog.created_at, Date).desc())
    )

    rows = result.all()
    daily = {}
    for row in rows:
        day_str = row.day.isoformat() if row.day else "unknown"
        if day_str not in daily:
            daily[day_str] = {"date": day_str, "services": {}, "total_calls": 0, "total_cost_cents": 0, "success_count": 0, "fail_count": 0}
        daily[day_str]["services"][row.service] = {
            "calls": row.total_calls,
            "cost_cents": row.total_cost_cents or 0,
            "success": row.success_count or 0,
            "fail": row.fail_count or 0,
        }
        daily[day_str]["total_calls"] += row.total_calls
        daily[day_str]["total_cost_cents"] += row.total_cost_cents or 0
        daily[day_str]["success_count"] += row.success_count or 0
        daily[day_str]["fail_count"] += row.fail_count or 0

    return {"daily": list(daily.values()), "days": days}


@router.get("/usage/by-user")
async def usage_by_user(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=30, ge=1, le=90),
):
    """Per-user usage breakdown."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    result = await db.execute(
        select(
            ApiUsageLog.user_id,
            func.count().label("total_calls"),
            func.sum(ApiUsageLog.cost_cents).label("total_cost_cents"),
            func.sum(case((ApiUsageLog.success == True, 1), else_=0)).label("success_count"),
        )
        .where(ApiUsageLog.created_at >= cutoff)
        .group_by(ApiUsageLog.user_id)
        .order_by(func.sum(ApiUsageLog.cost_cents).desc())
    )

    users = []
    for row in result.all():
        u = await db.get(User, row.user_id) if row.user_id else None
        users.append({
            "user_id": row.user_id,
            "email": u.email if u else "unknown",
            "total_calls": row.total_calls,
            "total_cost_cents": row.total_cost_cents or 0,
            "success_rate": round((row.success_count or 0) / max(row.total_calls, 1) * 100, 1),
        })

    return {"users": users, "days": days}


@router.get("/usage/by-service")
async def usage_by_service(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=30, ge=1, le=90),
):
    """Per-service usage breakdown with chart data."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    result = await db.execute(
        select(
            ApiUsageLog.service,
            func.count().label("total_calls"),
            func.sum(ApiUsageLog.cost_cents).label("total_cost_cents"),
            func.avg(ApiUsageLog.duration_seconds).label("avg_duration"),
            func.sum(case((ApiUsageLog.success == True, 1), else_=0)).label("success_count"),
            func.sum(case((ApiUsageLog.success == False, 1), else_=0)).label("fail_count"),
        )
        .where(ApiUsageLog.created_at >= cutoff)
        .group_by(ApiUsageLog.service)
        .order_by(func.sum(ApiUsageLog.cost_cents).desc())
    )

    services = []
    for row in result.all():
        services.append({
            "service": row.service,
            "total_calls": row.total_calls,
            "total_cost_cents": row.total_cost_cents or 0,
            "avg_duration_seconds": round(row.avg_duration or 0, 2),
            "success_count": row.success_count or 0,
            "fail_count": row.fail_count or 0,
            "success_rate": round((row.success_count or 0) / max(row.total_calls, 1) * 100, 1),
        })

    return {"services": services, "days": days}


@router.get("/usage/recent")
async def usage_recent(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Last N API calls with full details."""
    result = await db.execute(
        select(ApiUsageLog)
        .order_by(ApiUsageLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()

    items = []
    for log in logs:
        items.append({
            "id": log.id,
            "user_id": log.user_id,
            "service": log.service,
            "operation": log.operation,
            "duration_seconds": log.duration_seconds,
            "cost_cents": log.cost_cents or 0,
            "success": log.success,
            "error_message": log.error_message,
            "runpod_job_id": log.runpod_job_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {"logs": items, "total": len(items)}


# ── System Status ──


@router.get("/status")
async def system_status(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Services health, container uptime, DB stats."""
    import psutil
    from config import settings

    # Server health
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime_seconds = int((datetime.now() - boot_time).total_seconds())
        server = {
            "status": "healthy",
            "cpu_percent": cpu,
            "memory_percent": memory.percent,
            "memory_used_gb": round((memory.total - memory.available) / (1024**3), 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "uptime_seconds": uptime_seconds,
        }
    except Exception:
        server = {"status": "unknown", "uptime_seconds": 0}

    # GPU Server status
    gpu_status = {"status": "disabled", "url": settings.GPU_SERVER_URL}
    if settings.GPU_SERVER_ENABLED and settings.GPU_SERVER_URL:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{settings.GPU_SERVER_URL}/health")
                if resp.status_code == 200:
                    gpu_status = {"status": "healthy", "url": settings.GPU_SERVER_URL, **resp.json()}
                else:
                    gpu_status = {"status": "unhealthy", "url": settings.GPU_SERVER_URL}
        except Exception:
            gpu_status = {"status": "unreachable", "url": settings.GPU_SERVER_URL}

    # RunPod endpoints
    runpod_endpoints = {
        "bs_roformer": settings.BS_ROFORMER_ENDPOINT_ID or "not configured",
        "fish_speech": settings.FISH_SPEECH_ENDPOINT_ID or "not configured",
        "infinitetalk": settings.RUNPOD_ENDPOINT_ID or "not configured",
    }

    # DB table counts
    table_counts = {}
    for model, name in [
        (Avatar, "avatars"), (Cast, "casts"),
        (Product, "products"), (User, "users"),
        (StreamSession, "stream_sessions"),
        (ApiUsageLog, "api_usage_logs"),
    ]:
        try:
            cnt = await db.execute(select(func.count(model.id)))
            table_counts[name] = cnt.scalar() or 0
        except Exception:
            table_counts[name] = -1

    # R2 status (just config check, not live query)
    r2_status = {
        "configured": bool(settings.R2_ACCESS_KEY_ID),
        "bucket": settings.R2_BUCKET,
        "public_url": settings.R2_PUBLIC_URL,
    }

    # Zernio — a single, platform-wide API key (settings.ZERNIO_API_KEY),
    # not per-user, so a failure (e.g. the shared account hitting its own
    # plan's post-limit) affects every user until someone on our side
    # notices and upgrades/fixes it. Sentry gets an event on every failure
    # (see _raise_zernio_error in routers/social.py), but this surfaces the
    # same signal here too so an admin checking this page doesn't have to
    # go dig through Sentry to see it's an ongoing problem, not a one-off.
    zernio_failures_24h = 0
    zernio_last_error = None
    try:
        from models.social_post import SocialPost
        cutoff = datetime.utcnow() - timedelta(hours=24)  # naive UTC to match DB column
        result = await db.execute(
            select(SocialPost)
            .where(SocialPost.status == "failed", SocialPost.created_at >= cutoff)
            .order_by(SocialPost.created_at.desc())
        )
        failed_posts = result.scalars().all()
        zernio_failures_24h = len(failed_posts)
        if failed_posts:
            zernio_last_error = failed_posts[0].error_message
    except Exception as exc:
        sentry_sdk.capture_exception(exc)

    zernio_status = {
        "configured": bool(settings.ZERNIO_API_KEY),
        "failures_24h": zernio_failures_24h,
        "last_error": zernio_last_error,
    }

    return {
        "server": server,
        "gpu_server": gpu_status,
        "runpod_endpoints": runpod_endpoints,
        "r2_storage": r2_status,
        "database": table_counts,
        "sentry_configured": bool(settings.SENTRY_DSN_BACKEND),
        "zernio": zernio_status,
    }


@router.post("/backfill-thumbnails")
async def backfill_thumbnails(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """One-off: generate poster thumbnails for existing ready renders that have none."""
    import sentry_sdk
    from models.cast_render import CastRender
    from services.thumbnail_service import generate_and_upload_thumbnail

    result = await db.execute(
        select(CastRender).where(
            CastRender.status == "ready",
            CastRender.thumbnail_key.is_(None),
            CastRender.output_video_r2_key.isnot(None),
        )
    )
    renders = result.scalars().all()
    succeeded = 0
    failed = 0

    for render in renders:
        try:
            thumb_key = await generate_and_upload_thumbnail(
                render.output_video_r2_key,
                render.id,
                render.duration_seconds,
            )
            if thumb_key:
                render.thumbnail_key = thumb_key
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            sentry_sdk.capture_exception(e)
            failed += 1

    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="admin.backfill_thumbnails", entity_type="admin",
            entity_id=None, after={"total": len(renders), "succeeded": succeeded, "failed": failed},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"total": len(renders), "succeeded": succeeded, "failed": failed}
