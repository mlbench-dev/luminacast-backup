import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User
from models.layout_template import LayoutTemplate
from routers.auth import get_current_user
from services import audit_log
import sentry_sdk

router = APIRouter(prefix="/api/layout-templates", tags=["layouts"])


class LayoutTemplateResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    name: str
    config: dict
    is_preset: bool
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class LayoutTemplateCreate(BaseModel):
    name: str
    config: dict


class LayoutTemplateListResponse(BaseModel):
    templates: list[LayoutTemplateResponse]
    total: int


def _to_response(lt: LayoutTemplate) -> LayoutTemplateResponse:
    return LayoutTemplateResponse(
        id=lt.id,
        user_id=lt.user_id,
        name=lt.name,
        config=lt.config,
        is_preset=lt.is_preset,
        created_at=lt.created_at.isoformat() if lt.created_at else None,
    )


@router.get("", response_model=LayoutTemplateListResponse)
async def list_layout_templates(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's custom layouts + all presets."""
    result = await db.execute(
        select(LayoutTemplate)
        .where(or_(LayoutTemplate.is_preset == True, LayoutTemplate.user_id == user.id))
        .order_by(LayoutTemplate.is_preset.desc(), LayoutTemplate.created_at)
    )
    templates = result.scalars().all()
    return LayoutTemplateListResponse(
        templates=[_to_response(t) for t in templates],
        total=len(templates),
    )


@router.post("", response_model=LayoutTemplateResponse, status_code=201)
async def create_layout_template(
    req: LayoutTemplateCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a custom layout template."""
    lt = LayoutTemplate(
        id=f"lt_{uuid.uuid4().hex[:12]}",
        user_id=user.id,
        name=req.name,
        config=req.config,
        is_preset=False,
    )
    db.add(lt)
    await db.commit()
    await db.refresh(lt)
    try:
        await audit_log.record(
            db, user_id=user.id, action="layout.create", entity_type="layout",
            entity_id=lt.id, after={"name": lt.name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _to_response(lt)


@router.put("/{template_id}", response_model=LayoutTemplateResponse)
async def update_layout_template(
    template_id: str,
    req: LayoutTemplateCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a custom layout template. Presets cannot be updated."""
    lt = await db.get(LayoutTemplate, template_id)
    if not lt or lt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Layout template not found")
    if lt.is_preset:
        raise HTTPException(status_code=400, detail="Cannot edit preset layouts")

    lt.name = req.name
    lt.config = req.config
    await db.commit()
    await db.refresh(lt)
    try:
        await audit_log.record(
            db, user_id=user.id, action="layout.update", entity_type="layout",
            entity_id=template_id, after={"name": lt.name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _to_response(lt)


@router.delete("/{template_id}", status_code=204)
async def delete_layout_template(
    template_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a custom layout template. Presets cannot be deleted."""
    lt = await db.get(LayoutTemplate, template_id)
    if not lt or lt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Layout template not found")
    if lt.is_preset:
        raise HTTPException(status_code=400, detail="Cannot delete preset layouts")

    try:
        await audit_log.record(
            db, user_id=user.id, action="layout.delete", entity_type="layout",
            entity_id=template_id, before={"name": lt.name},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
    await db.delete(lt)
    await db.commit()
