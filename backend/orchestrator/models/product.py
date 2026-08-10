from database import Base
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True)                  # prefix: prod_
    user_id = Column(String, ForeignKey("users.id"), index=True)

    # TikTok Shop data
    tiktok_product_id = Column(String, index=True, nullable=True)
    tiktok_product_url = Column(String, nullable=True)
    title = Column(String, nullable=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    product_url = Column(String, default="")
    price = Column(Float, nullable=False)
    current_price = Column(Float, default=0)
    original_price = Column(Float, default=0)
    discount_percent = Column(Float, default=0)
    # No default — a fake commission rate with no basis (e.g. sites with no
    # affiliate program at all) is worse than showing nothing. Left null
    # until a real one is resolved (Amazon category lookup, TikTok payload)
    # or the user enters one manually.
    commission_rate = Column(Float, nullable=True)
    commission_source = Column(String(30), nullable=True)    # tiktok_affiliate, amazon_associates, manual
    commission_category = Column(String(100), nullable=True) # matched Amazon category
    affiliate_tag = Column(String(100), nullable=True)       # user's Amazon tag or TikTok affiliate ID
    sales_volume = Column(Integer, default=0)
    rating = Column(Float, default=0)
    review_count = Column(Integer, default=0)
    seller_name = Column(String, default="")
    category = Column(String, default="")
    tags = Column(JSON, default=list)

    cover_image_key = Column(String, default="")
    media_keys = Column(JSON, nullable=True)

    # pro100chok product-mode rich fields
    full_description = Column(Text, nullable=True)
    variants = Column(JSON, nullable=True)
    specifications = Column(JSON, nullable=True)     # list of [{name, value}]
    selling_points = Column(JSON, nullable=True)
    shop_rating = Column(Float, nullable=True)
    shop_followers = Column(Integer, nullable=True)
    shop_identity_label = Column(String(120), nullable=True)
    store_sub_scores = Column(JSON, nullable=True)
    sold_last_30_days = Column(Integer, nullable=True)
    overlay_key = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)

    assets = relationship("ProductAsset", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_products_user_tiktok", "user_id", "tiktok_product_id", unique=True,
              postgresql_where=Column("tiktok_product_id").isnot(None)),
    )
