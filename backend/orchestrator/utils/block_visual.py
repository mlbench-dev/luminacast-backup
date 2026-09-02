"""Which blocks own their visual — so b-roll must never be layered over them.

An ``avatar_action`` (motion / body-motion) block's whole point is the
generated clip of the avatar performing the action. A full-frame b-roll
overlay on such a block just covers that clip — the viewer sees stock footage
instead of the action. These blocks therefore never carry ``parallel_media`` /
stock b-roll; the guards that call this helper strip it on save and on
outline→block creation.
"""
from __future__ import annotations

# Categories whose OWN animated clip IS the block's visual. Kept in sync with
# the frontend LEGACY_CATEGORY_ALIASES (avatar_motion / avatar_acting /
# avatar_body_motion all collapse to avatar_action).
_MOTION_VISUAL_CATEGORIES = frozenset(
    {
        "avatar_action",
        "avatar_acting",
        "avatar_motion",
        "avatar_body_motion",
    }
)
_MOTION_VISUAL_RENDER_MODES = frozenset({"motion", "body_motion"})


def block_owns_its_visual(category: object, render_mode: object = None) -> bool:
    """True when the block's own animated clip is the visual, so
    ``parallel_media`` / stock b-roll must not be attached to it."""
    cat = category.strip().lower() if isinstance(category, str) else ""
    rm = render_mode.strip().lower() if isinstance(render_mode, str) else ""
    return cat in _MOTION_VISUAL_CATEGORIES or rm in _MOTION_VISUAL_RENDER_MODES
