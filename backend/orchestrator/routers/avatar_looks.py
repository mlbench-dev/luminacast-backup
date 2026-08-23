import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.avatar import Avatar
from models.avatar_look import AvatarLook, SceneEnvironment, DEFAULT_ENVIRONMENT
from models.user import User
from routers.auth import get_current_user
from services.r2_storage import get_r2_storage_service

router = APIRouter(prefix="/api/avatar", tags=["avatar-looks"])
logger = logging.getLogger(__name__)

STUCK_LOOK_AGE = timedelta(minutes=10)


def _look_to_dict(look: AvatarLook) -> dict:
    r2 = get_r2_storage_service()
    return {
        "id": look.id,
        "avatar_id": look.avatar_id,
        "name": look.name,
        "background_prompt": look.background_prompt,
        "face_ref_key": look.face_ref_key,
        "image_url": r2.get_public_url(look.face_ref_key) if look.face_ref_key else None,
        "is_default": look.is_default,
        "is_original": look.is_original,
        "status": look.status,
        "error_message": look.error_message,
        "look_type": look.look_type or "background",
        # Round-6 Bug B: a look is keyed by (avatar_id, look_type, framing);
        # surface framing so the editor / debug can see which shot it is.
        "framing": look.framing or "MEDIUM",
        "pose_angle": look.pose_angle,
        "product_id": look.product_id,
        "created_at": look.created_at.isoformat() if look.created_at else None,
        # Scene-aware voice filters: chosen when the scene is created (below),
        # not decided later per-block. See services/mic_presets.py.
        "environment": look.environment or DEFAULT_ENVIRONMENT,
        "mic_visible": bool(look.mic_visible),
    }


class CreateLookRequest(BaseModel):
    name: str
    background_prompt: str = ""
    look_type: str = "background"
    pose_angle: Optional[str] = None
    product_id: Optional[str] = None
    # Scene properties, decided at creation time instead of only per-block
    # afterward. Optional — omitted looks keep the model defaults (studio,
    # mic off).
    environment: Optional[str] = None
    mic_visible: Optional[bool] = None


@router.get("/{avatar_id}/looks")
async def list_avatar_looks(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    result = await db.execute(
        select(AvatarLook)
        .where(AvatarLook.avatar_id == avatar_id)
        .order_by(AvatarLook.is_original.desc(), AvatarLook.is_default.desc(), AvatarLook.created_at.asc())
    )
    looks = result.scalars().all()
    return {"looks": [_look_to_dict(l) for l in looks]}


@router.post("/{avatar_id}/looks")
async def create_avatar_look(
    avatar_id: str,
    payload: CreateLookRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    if not avatar.face_ref_key:
        raise HTTPException(400, "Parent avatar has no face image yet")

    # Validate look_type
    valid_types = ("background", "body_motion", "tryon")
    if payload.look_type not in valid_types:
        raise HTTPException(400, f"Invalid look_type. Must be one of: {valid_types}")

    # Validate pose_angle for body_motion
    valid_poses = ("front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back")
    if payload.look_type == "body_motion" and payload.pose_angle not in valid_poses:
        raise HTTPException(400, f"body_motion looks require pose_angle. Must be one of: {valid_poses}")

    # Validate product_id for tryon
    if payload.look_type == "tryon" and not payload.product_id:
        raise HTTPException(400, "tryon looks require product_id")

    # Validate environment (scene-aware voice filters key off this)
    valid_environments = {e.value for e in SceneEnvironment}
    environment = (payload.environment or DEFAULT_ENVIRONMENT).strip().lower()
    if environment not in valid_environments:
        raise HTTPException(400, f"Invalid environment. Must be one of: {sorted(valid_environments)}")

    # Rate limit: only one in-flight look generation per avatar at a time.
    # Scope is per-avatar (not per-user) so working on multiple avatars in
    # parallel doesn't get blocked. Stuck looks older than STUCK_LOOK_AGE
    # are auto-failed here so a crashed worker can't permanently lock the
    # user out of creating new looks.
    in_flight_q = await db.execute(
        select(AvatarLook)
        .where(AvatarLook.avatar_id == avatar_id)
        .where(AvatarLook.status.in_(["pending", "generating"]))
    )
    in_flight = in_flight_q.scalars().all()
    if in_flight:
        now = datetime.now(timezone.utc)
        still_active = []
        for stuck in in_flight:
            created = stuck.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created is None or (now - created) > STUCK_LOOK_AGE:
                stuck.status = "failed"
                stuck.error_message = (
                    "AI background generation timed out before reporting back. "
                    "The previous attempt was cleared so you can try again."
                )
                logger.warning(
                    "Auto-failed stuck avatar look %s (avatar=%s, status=%s, age=%s)",
                    stuck.id,
                    avatar_id,
                    stuck.status,
                    (now - created) if created else "unknown",
                )
            else:
                still_active.append(stuck)
        await db.commit()
        if still_active:
            raise HTTPException(
                429,
                "An AI background is still being generated for this avatar. "
                "It usually finishes within a minute — please wait for it to complete.",
            )

    look_id = f"al_{uuid.uuid4().hex[:12]}"
    look = AvatarLook(
        id=look_id,
        avatar_id=avatar_id,
        name=payload.name[:200],
        background_prompt=payload.background_prompt[:1000] if payload.background_prompt else "",
        is_default=False,
        status="pending",
        look_type=payload.look_type,
        pose_angle=payload.pose_angle if payload.look_type == "body_motion" else None,
        product_id=payload.product_id if payload.look_type == "tryon" else None,
        environment=environment,
        # Preserve None (caller didn't set it) rather than coercing to
        # False — a real True/False here now outranks the block's template
        # mic_on default (see mic_presets.resolve_scene_voice_settings), so
        # coercing an omitted value to False would silently make every
        # look-without-an-explicit-choice look deliberately mic-off.
        mic_visible=payload.mic_visible,
    )
    db.add(look)
    await db.commit()

    from tasks.avatar_looks import generate_avatar_look_task
    generate_avatar_look_task.delay(look_id)

    return _look_to_dict(look)


class UpdateLookRequest(BaseModel):
    # Scene properties only — everything else about a look (background_prompt,
    # look_type, etc.) is fixed at creation time. This lets a scene's
    # mic-visible state be flipped in place (e.g. from the picker's per-scene
    # toggle) without regenerating the image or losing the existing
    # mic_visible=True variant already produced by generate_mic_on_variant.
    environment: Optional[str] = None
    mic_visible: Optional[bool] = None


@router.patch("/{avatar_id}/looks/{look_id}")
async def update_avatar_look(
    avatar_id: str,
    look_id: str,
    payload: UpdateLookRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    look = await db.get(AvatarLook, look_id)
    if not look or look.avatar_id != avatar_id:
        raise HTTPException(404, "Look not found")

    if payload.environment is not None:
        valid_environments = {e.value for e in SceneEnvironment}
        environment = payload.environment.strip().lower()
        if environment not in valid_environments:
            raise HTTPException(400, f"Invalid environment. Must be one of: {sorted(valid_environments)}")
        look.environment = environment

    if payload.mic_visible is not None:
        look.mic_visible = payload.mic_visible
        if look.mic_visible and look.face_ref_key:
            # Make sure the baked clip-on variant exists so the very next
            # render doesn't pay the FLUX generation latency on the
            # critical path — resolve_mic_on_face_key would lazy-generate
            # it anyway, but doing it here means the toggle's effect is
            # ready immediately rather than on next render. Never raises
            # (see generate_mic_on_variant's own docstring/failure policy).
            from services.mic_on_look import generate_mic_on_variant
            await generate_mic_on_variant(avatar_id, look_id, db)

    await db.commit()
    await db.refresh(look)
    return _look_to_dict(look)


@router.delete("/{avatar_id}/looks/{look_id}")
async def delete_avatar_look(
    avatar_id: str,
    look_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    look = await db.get(AvatarLook, look_id)
    if not look or look.avatar_id != avatar_id:
        raise HTTPException(404, "Look not found")

    if look.is_default:
        raise HTTPException(400, "Cannot delete the default look. Set another as default first.")

    if look.is_original:
        raise HTTPException(400, "Cannot delete the original look.")

    await db.delete(look)
    await db.commit()
    return {"deleted": True}


@router.post("/{avatar_id}/looks/{look_id}/set-default")
async def set_default_look(
    avatar_id: str,
    look_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    look = await db.get(AvatarLook, look_id)
    if not look or look.avatar_id != avatar_id:
        raise HTTPException(404, "Look not found")

    if look.status != "ready":
        raise HTTPException(400, "Look is not ready yet")

    # Clear all defaults for this avatar, then set the new one
    all_looks = await db.execute(
        select(AvatarLook).where(AvatarLook.avatar_id == avatar_id)
    )
    for l in all_looks.scalars():
        l.is_default = (l.id == look_id)
    await db.commit()
    return _look_to_dict(look)




@router.post("/{avatar_id}/generate-all-body-motion")
async def generate_all_body_motion(
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create all missing body motion pose AvatarLook records and process them in a single Celery task."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    if not avatar.face_ref_key:
        raise HTTPException(400, "Parent avatar has no face image yet")

    ALL_POSES = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"]
    POSE_LABELS = {
        "front": "Front", "three_quarter_left": "3/4 Left", "three_quarter_right": "3/4 Right",
        "profile_left": "Profile Left", "profile_right": "Profile Right", "back": "Back",
    }

    # Find existing body motion looks
    existing_result = await db.execute(
        select(AvatarLook).where(
            AvatarLook.avatar_id == avatar_id,
            AvatarLook.look_type == "body_motion",
        )
    )
    existing_looks = existing_result.scalars().all()
    covered = {l.pose_angle for l in existing_looks if l.status in ("ready", "pending", "generating")}

    missing_poses = [p for p in ALL_POSES if p not in covered]
    if not missing_poses:
        return {"message": "All poses already exist", "created": 0, "look_ids": []}

    # Create AvatarLook records for all missing poses
    look_ids = []
    for pose in missing_poses:
        look_id = f"al_{uuid.uuid4().hex[:12]}"
        look = AvatarLook(
            id=look_id,
            avatar_id=avatar_id,
            name=f"AI: {POSE_LABELS.get(pose, pose)}",
            background_prompt=f"Body motion pose: {pose}",
            is_default=False,
            status="pending",
            look_type="body_motion",
            pose_angle=pose,
        )
        db.add(look)
        look_ids.append(look_id)

    await db.commit()

    # Dispatch single Celery task that processes them sequentially
    from tasks.avatar_looks import generate_all_body_motion_task
    generate_all_body_motion_task.delay(avatar_id, look_ids)

    return {
        "message": f"Generating {len(look_ids)} missing poses",
        "created": len(look_ids),
        "look_ids": look_ids,
        "total_poses": len(ALL_POSES),
    }