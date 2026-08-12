from typing import Optional

from pydantic import BaseModel, Field


class SubscriptionCheckoutRequest(BaseModel):
    plan: str = Field(..., description="starter | pro | studio")
    interval: str = Field(..., description="month | year")


class CreditsCheckoutRequest(BaseModel):
    pack_id: str = Field(..., description="credits_20 | credits_50 | credits_100")


class AutoTopupRequest(BaseModel):
    enabled: bool
    threshold_cents: Optional[int] = Field(None, ge=0)
    amount_cents: Optional[int] = Field(None, gt=0)


class PortalRequest(BaseModel):
    return_url: Optional[str] = None
