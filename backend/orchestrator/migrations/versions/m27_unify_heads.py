"""unify all open migration heads — 2026-08-08

Revision ID: m27_unify_heads
Revises: 1419fc1f6295, cb66416bce46, ug93epbrdc9q, chore_live_mode_payload, se01_avatar_look_scene_env
Create Date: 2026-08-08

Several migrations (cast_version_and_detected_language, add_body_motion_frame_prompts,
add_timezone_to_users) were originally authored with colliding revision IDs
(two different files each claiming `a1b2c3d4e5f6`, two each claiming
`a8b9c0d1e2f3`), which alembic reported as "Cycle is detected in revisions".
Fixing the collisions (renaming to unique IDs: 1419fc1f6295, cb66416bce46,
ug93epbrdc9q) revealed that they — along with the unrelated
`chore_live_mode_payload` and `se01_avatar_look_scene_env` branches — were
never reconciled into a single head. Following the established repo pattern
(see m24_merge_all_heads, m26_05_05_unify_heads, lref01_add_live_references),
this empty merge migration unifies them.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "m27_unify_heads"
down_revision: Union[str, Sequence[str], None] = (
    "1419fc1f6295",
    "cb66416bce46",
    "ug93epbrdc9q",
    "chore_live_mode_payload",
    "se01_avatar_look_scene_env",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op; this migration only unifies the revision graph.
    pass


def downgrade() -> None:
    pass
