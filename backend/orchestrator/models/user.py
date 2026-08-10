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

    # Last workspace a user was operating in — a switcher hint only, not a
    # source of truth (a stale/revoked owner_id here is harmless; the
    # login flow re-validates it against live TeamMember rows). No FK:
    # the pointed-to owner's account could be deleted without needing to
    # touch every member who last happened to be viewing it.
    last_workspace_id = Column(String, nullable=True)

    avatars = relationship("Avatar", back_populates="user")
    # Explicit foreign_keys: Cast also has submitted_by/approved_by FKs to
    # users.id (Teams review workflow) — without this, SQLAlchemy can't
    # tell which of the three columns backs this relationship.
    casts = relationship("Cast", back_populates="user", foreign_keys="Cast.user_id")
    team_members = relationship("TeamMember", back_populates="owner", foreign_keys="TeamMember.owner_id")


class TeamRole(str, enum.Enum):
    """Per-workspace role for a TeamMember row.

    Deliberately separate from UserRole (which means "account holder" /
    global admin, not a workspace permission level) and deliberately
    lowercase — UserRole's values are uppercase and the frontend compares
    against a lowercase copy, a casing mismatch that silently breaks the
    admin checks in Sidebar.tsx/ProtectedRoute.tsx. New code shouldn't
    repeat that: backend and frontend TeamRole values match exactly.
    """
    VIEWER = "viewer"
    CREATOR = "creator"
    PUBLISHER = "publisher"


class TeamMemberStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class TeamMember(Base):
    __tablename__ = "team_members"
    id = Column(String, primary_key=True)  # prefix: tm_
    owner_id = Column(String, ForeignKey("users.id"), index=True)
    # Null until the invitee accepts and gets a real User row of their own.
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=True)
    # The email the invite was sent to — needed to identify/display a
    # still-pending row before user_id exists.
    invited_email = Column(String, index=True, nullable=True)
    role = Column(String, default=TeamRole.VIEWER.value)
    status = Column(String(20), nullable=False, default=TeamMemberStatus.PENDING.value)
    invited_at = Column(DateTime, server_default=func.now())
    accepted_at = Column(DateTime, nullable=True)
    # Revoked rows are kept (never deleted) so access can be re-checked on
    # every request without ambiguity, and so the member's history/audit
    # trail survives being removed from the team.
    revoked_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, onupdate=func.now(), nullable=True)

    owner = relationship("User", foreign_keys=[owner_id], back_populates="team_members")
    user = relationship("User", foreign_keys=[user_id])
