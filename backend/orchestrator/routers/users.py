"""User profile endpoints — interests, preferences, profile updates."""
import sentry_sdk
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models.user import User
from routers.auth import get_current_user
from services import audit_log

router = APIRouter(prefix="/api/users", tags=["users"])


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
