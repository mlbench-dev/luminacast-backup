from database import Base
from sqlalchemy import Column, String, DateTime, Integer, JSON, ForeignKey
from sqlalchemy.sql import func


class ScrapingJob(Base):
    __tablename__ = "scraping_jobs"
    id = Column(String, primary_key=True)               # prefix: scrape_
    user_id = Column(String, ForeignKey("users.id"), index=True)
    platform = Column(String, nullable=False, index=True)  # tiktok | instagram | youtube | etc.
    handle = Column(String, nullable=False)
    normalized_url = Column(String, nullable=True)
    status = Column(String, default="completed")        # pending | completed | failed
    video_count = Column(Integer, default=0)
    result_data = Column(JSON, nullable=True)            # cached video metadata array
    apify_run_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)         # cache expiry (e.g. 24 hours)
