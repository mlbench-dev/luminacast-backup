from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class SessionSummary(BaseModel):
    id: str
    cast_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_minutes: float
    peak_viewers: int
    total_purchases: int
    total_gmv: float
    streaming_cost_cents: int
    afk_events: int

    model_config = {"from_attributes": True}


class SessionAnalyticsResponse(BaseModel):
    sessions: List[SessionSummary]
    total: int


class VariantPerformanceItem(BaseModel):
    variant_id: str
    block_id: str
    block_type: str
    product_name: Optional[str] = None
    times_played: int
    purchases_during: int
    performance_score: Optional[float] = None
    script_preview: Optional[str] = None


class VariantPerformanceResponse(BaseModel):
    variants: List[VariantPerformanceItem]
    total: int
