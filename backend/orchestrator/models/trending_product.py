from database import Base
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, Boolean, Text, Index
from sqlalchemy.sql import func


class TrendingProduct(Base):
    __tablename__ = "trending_products"

    id = Column(String, primary_key=True)                    # prefix: tp_

    # TikTok Shop data
    tiktok_product_id = Column(String, index=True, unique=True)
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    product_url = Column(String, default="")

    # Pricing
    current_price = Column(Float, default=0)
    original_price = Column(Float, default=0)
    discount_percent = Column(Float, default=0)

    # Sales intelligence
    revenue_cents = Column(Integer, default=0)
    revenue_growth_rate = Column(Float, default=0)
    items_sold = Column(Integer, default=0)
    avg_unit_price = Column(Float, default=0)

    # Ratings
    rating = Column(Float, default=0)
    review_count = Column(Integer, default=0)

    # Seller
    seller_name = Column(String, default="")
    seller_id = Column(String, default="")

    # Commission
    commission_rate = Column(Float, default=0)
    is_affiliate = Column(Boolean, default=False)

    # Creator stats
    creator_count = Column(Integer, default=0)

    # Category
    category = Column(String, default="", index=True)
    subcategory = Column(String, default="")

    # Media
    cover_image_url = Column(String, default="")
    cover_image_r2_key = Column(String, default="")
    additional_image_urls = Column(JSON, default=list)  # Array of TikTok CDN image URLs
    video_urls = Column(JSON, nullable=True)  # Array of product demo/review video URLs

    # Section / ranking
    section = Column(String, default="", index=True)
    rank_position = Column(Integer, default=0)

    # Sparkline data (last 30 days revenue as JSON array)
    revenue_trend = Column(JSON, default=list)
    revenue_trend_source = Column(String, default="empty")  # "empty" | "scraped" | "synthetic"

    # pro100chok product-mode rich fields (populated at import time)
    variants = Column(JSON, nullable=True)           # list of {variantId, name, price, stockStatus, stockQuantity, imageUrl}
    specifications = Column(JSON, nullable=True)     # dict {key: value} normalized to list [{name, value}] at ingest
    selling_points = Column(JSON, nullable=True)     # list of strings
    full_description = Column(Text, nullable=True)   # long form product description
    sold_last_30_days = Column(Integer, nullable=True)
    shop_rating = Column(Float, nullable=True)
    shop_followers = Column(Integer, nullable=True)
    store_sub_scores = Column(JSON, nullable=True)   # {name: {score, percentage}}
    experience_scores = Column(JSON, nullable=True)
    shop_identity_label = Column(String(120), nullable=True)
    ratings_breakdown = Column(JSON, nullable=True)  # {totalCount, overallScore, stars: {1..5}}
    enriched_at = Column(DateTime, nullable=True, index=True)

    # Cache metadata
    region = Column(String, default="US", index=True)
    scraped_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_trending_section_rank", "section", "rank_position"),
        Index("ix_trending_revenue", "revenue_cents"),
        Index("ix_trending_items_sold", "items_sold"),
        Index("ix_trending_commission", "commission_rate"),
    )
