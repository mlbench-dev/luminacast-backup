"""User profile endpoints — interests, preferences, profile updates."""
import os
import uuid
from io import BytesIO

import sentry_sdk
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models.user import User
from routers.auth import get_current_user
from schemas.auth import UpdateProfileRequest, UserResponse
from services import audit_log
from services.r2_storage import get_r2_storage_service

router = APIRouter(prefix="/api/users", tags=["users"])

AVATAR_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB — a profile picture, not a media asset
AVATAR_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AVATAR_CONTENT_TYPE_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


# Two-platform whitelist for the affiliate connect flow. The "+ Add
# affiliate account" button in Settings is documented as future-only
# (spec FEATURE 3.1) -- when we add a third platform, extend this set
# AND the User model in the same PR.
_AFFILIATE_PLATFORMS = {"tiktok", "amazon"}
_AFFILIATE_FIELD = {
    "tiktok": "tiktok_affiliate_id",
    "amazon": "amazon_associate_tag",
}
_AFFILIATE_MAX_LEN = 100


class UpdateInterestsRequest(BaseModel):
    interests: list[str]  # e.g. ["fitness", "beauty", "tech", "cooking"]


class UpdateInterestsResponse(BaseModel):
    custom_interests: list[str]


class UpdateAffiliateRequest(BaseModel):
    platform: str        # "tiktok" | "amazon"
    value: Optional[str] = None  # empty / None / whitespace-only clears the field


class AffiliateAccountsResponse(BaseModel):
    tiktok_affiliate_id: Optional[str] = None
    amazon_associate_tag: Optional[str] = None


@router.post("/me/interests", response_model=UpdateInterestsResponse)
async def update_my_interests(
    req: UpdateInterestsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add or replace the current user's custom interest tags.

    These interests are used by the script generator to tailor content
    to the creator's niche beyond their avatar's target audience.
    """
    try:
        # Deduplicate and normalize
        cleaned = list(dict.fromkeys(
            tag.strip().lower() for tag in req.interests if tag.strip()
        ))

        if len(cleaned) > 50:
            raise HTTPException(
                status_code=422,
                detail="Maximum 50 interest tags allowed",
            )

        user.custom_interests = cleaned
        await db.commit()
        await db.refresh(user)

        try:
            await audit_log.record(
                db, user_id=user.id, action="settings.interests_update", entity_type="settings",
                entity_id=user.id, after={"interests": cleaned},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

        return UpdateInterestsResponse(custom_interests=user.custom_interests or [])
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail="Failed to update interests")


@router.get("/me/interests", response_model=UpdateInterestsResponse)
async def get_my_interests(
    user: User = Depends(get_current_user),
):
    """Get the current user's custom interest tags."""
    return UpdateInterestsResponse(custom_interests=user.custom_interests or [])


# ── Affiliate accounts ──

@router.get("/me/affiliates", response_model=AffiliateAccountsResponse)
async def get_my_affiliates(user: User = Depends(get_current_user)):
    """Return the current user's affiliate IDs.

    Both fields are nullable. The frontend uses NULL / empty to mean
    "platform is not connected" and surfaces the appropriate CTA.
    """
    return AffiliateAccountsResponse(
        tiktok_affiliate_id=user.tiktok_affiliate_id,
        amazon_associate_tag=user.amazon_associate_tag,
    )


@router.patch("/me/affiliates", response_model=AffiliateAccountsResponse)
async def update_my_affiliate(
    req: UpdateAffiliateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear an affiliate ID for the given platform.

    Empty / whitespace-only / null `value` CLEARS the field — the user
    can disconnect a platform by submitting an empty string. Anything
    longer than 100 chars is rejected (matches the column length).
    """
    try:
        platform = (req.platform or "").strip().lower()
        if platform not in _AFFILIATE_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown platform '{platform}'. Supported: tiktok, amazon",
            )

        cleaned: Optional[str]
        if req.value is None:
            cleaned = None
        else:
            cleaned = req.value.strip()
            if not cleaned:
                cleaned = None
            elif len(cleaned) > _AFFILIATE_MAX_LEN:
                raise HTTPException(
                    status_code=422,
                    detail=f"Value exceeds {_AFFILIATE_MAX_LEN} characters",
                )

        setattr(user, _AFFILIATE_FIELD[platform], cleaned)
        await db.commit()
        await db.refresh(user)

        try:
            await audit_log.record(
                db, user_id=user.id, action="settings.affiliate_update", entity_type="settings",
                entity_id=user.id, after={"platform": platform, "set": bool(cleaned)},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

        return AffiliateAccountsResponse(
            tiktok_affiliate_id=user.tiktok_affiliate_id,
            amazon_associate_tag=user.amazon_associate_tag,
        )
    except HTTPException:
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=500, detail="Failed to update affiliate")


# ── Profile: display name + avatar image ──

def _user_response(user: User) -> UserResponse:
    resp = UserResponse.model_validate(user)
    if user.avatar_r2_key:
        resp.avatar_url = get_r2_storage_service().get_public_url(user.avatar_r2_key)
    return resp


@router.patch("/me/profile", response_model=UserResponse)
async def update_my_profile(
    req: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.display_name = req.display_name
    await db.commit()
    await db.refresh(user)

    try:
        await audit_log.record(
            db, user_id=user.id, action="settings.profile_update", entity_type="settings",
            entity_id=user.id, after={"display_name": user.display_name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _user_response(user)


@router.post("/me/avatar", response_model=UserResponse)
async def upload_my_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload/replace the profile picture shown in the sidebar and account
    settings. Always stored at a fixed per-user key (not one-per-upload
    like the cast media library) so old uploads don't accumulate in R2 —
    each new upload just overwrites the previous object."""
    original_name = file.filename or "avatar.jpg"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in AVATAR_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(AVATAR_ALLOWED_EXTENSIONS))
        raise HTTPException(400, f"File type {ext} not allowed. Use: {allowed}")

    content_type = file.content_type or AVATAR_CONTENT_TYPE_BY_EXT.get(ext, "image/jpeg")
    if content_type not in set(AVATAR_CONTENT_TYPE_BY_EXT.values()):
        content_type = AVATAR_CONTENT_TYPE_BY_EXT.get(ext, "image/jpeg")

    data = await file.read()
    if len(data) > AVATAR_MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {AVATAR_MAX_FILE_SIZE // (1024 * 1024)} MB.")

    try:
        from PIL import Image
        Image.open(BytesIO(data)).verify()
    except Exception:
        raise HTTPException(400, "File does not look like a valid image.")

    r2_key = f"users/{user.id}/avatar{ext}"
    try:
        r2 = get_r2_storage_service()
        await r2.upload_bytes(data, r2_key, content_type=content_type)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=502, detail="Could not upload image. Please try again.")

    # If a previous upload used a different extension, its object is
    # simply orphaned in R2 (no longer referenced) — this codebase doesn't
    # delete R2 objects anywhere (see user_photos.py's soft-delete-only
    # convention), so neither does this.
    user.avatar_r2_key = r2_key
    await db.commit()
    await db.refresh(user)

    try:
        await audit_log.record(
            db, user_id=user.id, action="settings.avatar_upload", entity_type="settings", entity_id=user.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _user_response(user)


@router.delete("/me/avatar", response_model=UserResponse)
async def delete_my_avatar(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.avatar_r2_key:
        user.avatar_r2_key = None
        await db.commit()
        await db.refresh(user)
    return _user_response(user)
