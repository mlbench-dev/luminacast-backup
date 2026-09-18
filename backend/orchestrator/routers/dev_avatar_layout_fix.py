"""Dev-only A/B comparison tool: crop vs. AI-generate for an avatar photo
that doesn't match a cast's layout.

Not admin-gated — "dev" here means "experimental/temporary," reachable by
any regular logged-in user on their own avatars/casts, same auth as normal
cast/avatar endpoints. See services/avatar_layout_fix.py for the two
strategies and the root-cause context.

  POST /api/dev/avatar-layout-fix/run
    {avatar_id, format_family, strategy, cast_id?} -> {r2_key, url, width, height}
  GET  /api/dev/avatar-layout-fix/{avatar_id}
    -> previously generated candidates for this avatar
"""
from __future__ import annotations

import uuid
from io import BytesIO
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.avatar import Avatar
from models.avatar_look import AvatarLook
from models.cast import Cast
from models.user import User, TeamRole
from routers.auth import get_current_user, require_role, WorkspaceContext
from services.avatar_layout_fix import crop_face_ref_to_format, generate_face_ref_for_format
from services.r2_storage import get_r2_storage_service

router = APIRouter(prefix="/api/dev/avatar-layout-fix", tags=["dev-avatar-layout-fix"])


class RunRequest(BaseModel):
    avatar_id: str
    format_family: str  # "vertical" | "horizontal"
    strategy: str  # "crop" | "generate"
    cast_id: Optional[str] = None


@router.post("/run")
async def run_avatar_layout_fix(
    req: RunRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    if req.format_family not in ("vertical", "horizontal"):
        raise HTTPException(400, "format_family must be 'vertical' or 'horizontal'")
    if req.strategy not in ("crop", "generate"):
        raise HTTPException(400, "strategy must be 'crop' or 'generate'")

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Avatar not found")
    if not avatar.face_ref_key:
        raise HTTPException(400, "Avatar has no face reference image yet")

    cast = None
    if req.cast_id:
        cast = await db.get(Cast, req.cast_id)
        if not cast or cast.user_id != ctx.workspace_owner_id:
            raise HTTPException(404, "Cast not found")

    r2 = get_r2_storage_service()
    face_url = r2.get_public_url(avatar.face_ref_key)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(face_url)
        resp.raise_for_status()
        source_bytes = resp.content

    if req.strategy == "crop":
        result_bytes = crop_face_ref_to_format(source_bytes, req.format_family)
    else:
        result_bytes = await generate_face_ref_for_format(face_url, req.format_family)

    from PIL import Image
    img = Image.open(BytesIO(result_bytes))
    width, height = img.width, img.height

    result_key = (
        f"dev/avatar-layout-fix/{req.avatar_id}/{req.format_family}/"
        f"{req.strategy}/{uuid.uuid4().hex[:12]}.jpg"
    )
    await r2.upload_bytes(result_bytes, result_key, content_type="image/jpeg")

    look = AvatarLook(
        id=f"al_{uuid.uuid4().hex[:12]}",
        avatar_id=req.avatar_id,
        name=f"Layout fix ({req.strategy}, {req.format_family})"[:200],
        face_ref_key=result_key,
        is_default=False,
        is_original=False,
        status="ready",
        look_type=f"layout_fix_{req.format_family}_{req.strategy}",
    )
    db.add(look)

    if cast is not None:
        cast.debug_face_ref_override_key = result_key

    await db.commit()

    return {
        "r2_key": result_key,
        "url": r2.get_public_url(result_key),
        "width": width,
        "height": height,
        "cast_id": req.cast_id,
    }


@router.get("/{avatar_id}")
async def list_avatar_layout_fix_candidates(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Avatar not found")

    result = await db.execute(
        select(AvatarLook)
        .where(
            AvatarLook.avatar_id == avatar_id,
            AvatarLook.look_type.like("layout_fix_%"),
            AvatarLook.status == "ready",
        )
        .order_by(AvatarLook.created_at.desc())
    )
    looks = result.scalars().all()
    r2 = get_r2_storage_service()
    candidates = []
    for lk in looks:
        # look_type shape: layout_fix_<format_family>_<strategy> — both
        # format_family ("vertical"/"horizontal") and strategy
        # ("crop"/"generate") are always single words, so splitting from the
        # right by 2 is safe regardless of the "layout_fix" prefix's own
        # underscore.
        parts = lk.look_type.rsplit("_", 2)
        format_family = parts[1] if len(parts) == 3 else "unknown"
        strategy = parts[2] if len(parts) == 3 else "unknown"
        candidates.append({
            "id": lk.id,
            "format_family": format_family,
            "strategy": strategy,
            "r2_key": lk.face_ref_key,
            "url": r2.get_public_url(lk.face_ref_key) if lk.face_ref_key else None,
            "created_at": lk.created_at.isoformat() if lk.created_at else None,
        })
    return {"avatar_id": avatar_id, "candidates": candidates}
