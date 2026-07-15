from database import Base
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class ProductAsset(Base):
    __tablename__ = "product_assets"

    id = Column(String, primary_key=True)                  # prefix: pa_
    product_id = Column(String, ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    asset_type = Column(String, nullable=False)
    # Video: "demo" | "unboxing" | "before_after" | "testimonial" | "reference" | "ai_generated_video"
    # Image: "product_shot" | "swatch" | "lifestyle" | "listing_photo" | "ai_generated_image"
    # Overlay: "price_overlay" | "badge_overlay" | "testimonial_overlay" | "custom_overlay"

    media_type = Column(String, nullable=False)            # "video" | "image" | "overlay"

    r2_key = Column(String, nullable=False)
    r2_url = Column(String, default="")
    thumbnail_r2_key = Column(String, default="")

    duration_seconds = Column(Float, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    file_size_bytes = Column(Integer, default=0)

    # Ordering for gallery / carousel display. 0 = cover, 1+ = additional media.
    position = Column(Integer, default=0, nullable=False)

    # For overlays
    overlay_config = Column(JSON, default=None)

    # For AI-generated assets
    generation_prompt = Column(String, default="")
    generation_model = Column(String, default="")

    created_at = Column(DateTime, server_default=func.now())
    product = relationship("Product", back_populates="assets")
