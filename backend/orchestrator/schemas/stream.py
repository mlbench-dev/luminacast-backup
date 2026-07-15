from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class StreamStartRequest(BaseModel):
    cast_id: str
    rtmp_url: str
    mode: str = "sequential"  # sequential | shuffle


class StreamStatusResponse(BaseModel):
    session_id: str
    cast_id: str
    status: str  # live | paused | idle | stopped
    uptime_seconds: float = 0
    viewers: int = 0
    total_purchases: int = 0
    total_gmv: float = 0
    current_block_position: Optional[int] = None
    current_block_type: Optional[str] = None
    streaming_cost_cents: int = 0


class StreamControlRequest(BaseModel):
    action: str  # skip | previous | pause | resume


class PinConfirmRequest(BaseModel):
    product_id: str
