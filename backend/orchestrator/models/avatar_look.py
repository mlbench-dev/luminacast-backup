from datetime import datetime
from database import Base
from sqlalchemy import Column, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum


class AvatarLookStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class ShotFraming(str, enum.Enum):
    """Camera framing for an avatar look (Round-6 Bug B).

    Every value maps to a FLUX prompt fragment (see ``FRAMING_PROMPTS``) so a
    block can request a distinct shot — the user wanted variety instead of the
    same medium-close framing on every avatar block.
    """
    CLOSE = "CLOSE"
    MEDIUM = "MEDIUM"
    MEDIUM_WIDE = "MEDIUM_WIDE"
    WIDE = "WIDE"
    ANGLE_LEFT_3Q = "ANGLE_LEFT_3Q"
    ANGLE_RIGHT_3Q = "ANGLE_RIGHT_3Q"


# FLUX prompt fragment per framing. Injected into the look-generation prompt so
# the rendered still actually carries the requested camera distance / angle.
FRAMING_PROMPTS: dict[str, str] = {
    ShotFraming.CLOSE.value: (
        "close-up portrait, head and shoulders, eye-level, shallow depth of field"
    ),
    ShotFraming.MEDIUM.value: "medium shot, chest-up, eye-level",
    ShotFraming.MEDIUM_WIDE.value: (
        "medium-wide shot, waist-up, slightly lower angle, more environment visible"
    ),
    ShotFraming.WIDE.value: "wide shot, full body partially visible, room context",
    ShotFraming.ANGLE_LEFT_3Q.value: (
        "three-quarter turn to camera-left, medium shot"
    ),
    ShotFraming.ANGLE_RIGHT_3Q.value: (
        "three-quarter turn to camera-right, medium shot"
    ),
}

DEFAULT_FRAMING = ShotFraming.MEDIUM.value


def framing_prompt_fragment(framing: str | None) -> str:
    """Return the FLUX prompt fragment for ``framing`` (defaults to MEDIUM)."""
    key = (framing or DEFAULT_FRAMING).strip().upper()
    return FRAMING_PROMPTS.get(key, FRAMING_PROMPTS[DEFAULT_FRAMING])


# look_type for a reusable per-avatar, per-framing talking-head face reference
# (Round-6 Bug B round-3). Unlike action/body-motion frames these are NOT keyed
# by block_id — one ready look per (avatar_id, framing) is shared across every
# lip-sync block that requests that framing.
TALKING_HEAD_LOOK_TYPE = "talking_head"

# Base FLUX prompt for a talking-head still. The caller appends the
# framing-specific fragment from ``framing_prompt_fragment``. Keep neutral,
# product-free and brand-consistent so the same reference works across blocks.
TALKING_HEAD_PROMPT_TEMPLATE = (
    "Same person, same face, same identity, same hair, same skin tone. "
    "A high-quality talking-head portrait, neutral confident expression, "
    "mouth slightly parted, vertical 9:16 framing, soft natural lighting, "
    "plain backdrop, shallow depth of field, premium creator look."
)


def talking_head_prompt(framing: str | None, style_hint: str | None = None) -> str:
    """Build the full talking-head generation prompt for ``framing``.

    ``style_hint`` is an optional per-avatar style addendum (e.g. wardrobe or
    mood) appended verbatim when present; pass ``None`` to use the neutral
    default. The framing fragment is always appended last so the rendered still
    carries the requested shot distance / angle.
    """
    parts = [TALKING_HEAD_PROMPT_TEMPLATE]
    hint = (style_hint or "").strip()
    if hint:
        parts.append(hint)
    parts.append(framing_prompt_fragment(framing))
    return " ".join(p.rstrip(". ") + "." for p in parts if p)


class AvatarLook(Base):
    __tablename__ = "avatar_looks"

    id = Column(String(40), primary_key=True)
    avatar_id = Column(String(40), ForeignKey("avatars.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    face_ref_key = Column(String(500), nullable=True)
    background_prompt = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    is_original = Column(Boolean, default=False, nullable=False, server_default="false")
    status = Column(String(20), default="pending", nullable=False)
    error_message = Column(Text, nullable=True)
    # look_type. Historic values: "background", "body_motion", "tryon".
    # The body-motion-frame feature mints per-block keys
    # ("body_motion_block_<block_id>_start" / "_end") so the column is
    # widened to 80 chars to fit them without truncation.
    look_type = Column(String(80), default="background", nullable=False)
    pose_angle = Column(String(20), nullable=True)
    # Camera framing (Round-6 Bug B). One of ShotFraming; default MEDIUM. A look
    # is keyed by the (avatar_id, look_type, framing) triple — a look generated
    # for one framing is NEVER reused for a different framing.
    framing = Column(String(20), default="MEDIUM", server_default="MEDIUM", nullable=False)
    product_id = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)

    avatar = relationship("Avatar", back_populates="looks")
