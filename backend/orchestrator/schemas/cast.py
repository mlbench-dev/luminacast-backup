from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class ProductCreate(BaseModel):
    name: str
    price: float = Field(..., gt=0)
    commission_rate: float = Field(default=0.15, ge=0, le=1)
    description: Optional[str] = None
    tiktok_product_url: Optional[str] = None
    media_keys: Optional[List[str]] = None


class ProductResponse(BaseModel):
    id: str
    name: str
    price: float
    commission_rate: float
    description: Optional[str] = None
    media_keys: Optional[List[str]] = None
    overlay_key: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BlockCreate(BaseModel):
    type: str  # BlockType enum value
    position: int = Field(..., ge=0)
    product_id: Optional[str] = None
    layout_mode: str = "full_avatar"  # LayoutMode enum value
    scene_image_key: Optional[str] = None
    auto_basket_enabled: bool = False
    auto_basket_timeout: int = 60
    mood: Optional[str] = None
    key_points: Optional[List[str]] = None
    chat_rules: Optional[dict] = None


class BlockResponse(BaseModel):
    id: str
    type: str
    position: int
    product_id: Optional[str] = None
    layout_mode: str
    scene_image_key: Optional[str] = None
    auto_basket_enabled: bool
    auto_basket_timeout: int
    mood: Optional[str] = None
    variant_count: int = 0

    model_config = {"from_attributes": True}


class CastCreate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    avatar_id: str
    template_name: Optional[str] = None
    # Stage-1 creative template the user picked (see services.cast_templates).
    # Optional; null means "Auto / let AI choose" — the legacy behavior where
    # the outline generator decides the structure freely. When set, the outline
    # generator is constrained to the template's block sequence + bias ratios.
    template_id: Optional[str] = None
    max_duration_minutes: int = Field(default=240, ge=1, le=480)
    loop: bool = True
    quality: str = "simple"  # simple | hd | hd_plus
    output_format: Optional[str] = "9:16"
    channel_id: Optional[str] = None
    product_ids: Optional[List[str]] = None  # References to existing products
    products: Optional[List[ProductCreate]] = None
    blocks: Optional[List[BlockCreate]] = None
    effects_config: Optional[dict] = None
    script_direction: Optional[str] = None
    cast_type: Optional[str] = "recorded"
    description: Optional[str] = None
    duration_target_seconds: Optional[int] = None
    platform_target: Optional[str] = "tiktok"
    target_platforms: Optional[List[str]] = None
    background_music_url: Optional[str] = None
    background_music_mood: Optional[str] = None
    background_music_tags: Optional[List[str]] = None
    # How background music is handled: "off" | "auto" | "track_id:<id>".
    # Defaults to "auto" (mood-driven Mubert generation).
    music_track_choice: Optional[str] = "auto"
    music_volume: Optional[float] = None
    caption_preset: Optional[dict] = None
    # Cast-wide avatar background look picked at SetupPhase. Optional;
    # null means "use the avatar's own default look". The smart outline
    # endpoint propagates this onto each new block's `avatar_look_id`.
    default_avatar_look_id: Optional[str] = None
    # Production level: quick | standard | premium. Constrains block-category
    # mix at outline time. Defaults to "standard" if omitted.
    production_level: Optional[str] = "standard"
    # PR #162 — Stage-1 LIVE/Recorded toggle payload. Both optional; null on a
    # Recorded cast. `live_mode_defaults` is the LIVE preset object (voiceover /
    # b-roll toggles, max duration, b-roll cadence); `user_video_ids` is the list
    # of UserVideoAsset ids the user picked as preferred b-roll sources.
    live_mode_defaults: Optional[dict] = None
    user_video_ids: Optional[List[str]] = None


class CastResponse(BaseModel):
    id: str
    name: Optional[str] = None
    status: str
    avatar_id: str
    template_name: Optional[str] = None
    template_id: Optional[str] = None
    max_duration_minutes: int
    loop: bool
    creation_fee_cents: Optional[int] = None
    creation_paid: bool
    generation_progress: float
    generation_error: Optional[str] = None
    quality: Optional[str] = "simple"
    output_format: Optional[str] = None
    cast_type: Optional[str] = "recorded"
    production_level: Optional[str] = "standard"
    total_clips: int = 0
    completed_clips: int = 0
    progress_step: Optional[str] = None
    effects_config: Optional[dict] = None
    script_direction: Optional[str] = None
    description: Optional[str] = None
    duration_target_seconds: Optional[int] = None
    platform_target: Optional[str] = None
    format_family: Optional[str] = None
    target_platforms: Optional[List[str]] = None
    version: int = 1
    final_video_url: Optional[str] = None
    music_track_choice: Optional[str] = "auto"
    music_volume: Optional[float] = None
    default_avatar_look_id: Optional[str] = None
    live_mode_defaults: Optional[dict] = None
    user_video_ids: Optional[List[str]] = None
    avatar_thumbnail_url: Optional[str] = None
    avatar_name: Optional[str] = None
    render_status: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CastListItem(CastResponse):
    """List-only enriched response. Carries avatar metadata so the cast
    card can render the avatar thumbnail without a per-cast follow-up
    call. Extra fields beyond CastResponse pass through via the dict
    return path in list_casts."""
    model_config = {"from_attributes": True, "extra": "allow"}


class CastListResponse(BaseModel):
    casts: List[CastResponse]
    total: int
    # Both null when the caller omitted `page` (legacy/unpaged callers).
    page: Optional[int] = None
    per_page: Optional[int] = None


class OutlineScene(BaseModel):
    block_type: str
    mood: str
    key_points: List[str]
    style_directives: List[str]
    estimated_duration_seconds: int
    product_name: Optional[str] = None


class OutlineResponse(BaseModel):
    cast_id: str
    scenes: List[OutlineScene]
    estimated_total_duration_seconds: int


class BulkBlockVariant(BaseModel):
    script_text: str = ""
    variant_label: str = "A"


class BulkBlock(BaseModel):
    type: str  # BlockType enum value
    position: int = Field(..., ge=0)
    product_id: Optional[str] = None
    mood: Optional[str] = None
    script_text: Optional[str] = None  # Convenience: creates a default variant
    variants: Optional[List[BulkBlockVariant]] = None


class BulkBlocksSave(BaseModel):
    blocks: List[BulkBlock]


class CastPayRequest(BaseModel):
    payment_method_id: Optional[str] = None


class GenerationStatusResponse(BaseModel):
    cast_id: str
    status: str
    progress: float
    current_step: Optional[str] = None
    failed_count: int = 0
    total_variants: int = 0
