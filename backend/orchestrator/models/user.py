import enum
from database import Base
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON, Float, Integer, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


class UserRole(str, enum.Enum):
    CREATOR = "CREATOR"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)  # prefix: usr_
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.CREATOR)
    stripe_customer_id = Column(String, unique=True, nullable=True)
    tiktok_handle = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    timezone = Column(String(50), nullable=True)  # IANA timezone, e.g. "America/New_York"
    custom_interests = Column(JSON, nullable=True)

    # Mubert v3 per-user credentials. Created on demand the first time the
    # user requests AI music; reused for every subsequent generation so we
    # don't burn a `service/customers` round-trip per track.
    mubert_customer_id = Column(String(80), nullable=True)
    mubert_access_token = Column(Text, nullable=True)

    # Affiliate platform credentials. Empty / NULL means the platform is
    # not connected — the product page surfaces a CTA to connect it.
    tiktok_affiliate_id = Column(String(100), nullable=True)
    amazon_associate_tag = Column(String(100), nullable=True)

    avatars = relationship("Avatar", back_populates="user")
    casts = relationship("Cast", back_populates="user")
    team_members = relationship("TeamMember", back_populates="owner", foreign_keys="TeamMember.owner_id")


class TeamMember(Base):
    __tablename__ = "team_members"
    id = Column(String, primary_key=True)  # prefix: tm_
    owner_id = Column(String, ForeignKey("users.id"), index=True)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    role = Column(String, default="chat_operator")
    invited_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    owner = relationship("User", foreign_keys=[owner_id], back_populates="team_members")
    user = relationship("User", foreign_keys=[user_id])
