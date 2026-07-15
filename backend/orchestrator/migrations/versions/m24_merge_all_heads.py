"""merge all heads before phase 2.4

Revision ID: m24_merge_all_heads
Revises: 0eacovpyb3e5, a8b9c0d1e2f3, ab02_clone_scout, d3d540cda4fa, f1a2b3c4d5e6, gv01_ai_gen_videos
Create Date: 2026-04-15

"""
from typing import Sequence, Union
from alembic import op

revision: str = "m24_merge_all_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0eacovpyb3e5",
    "a8b9c0d1e2f3",
    "ab02_clone_scout",
    "d3d540cda4fa",
    "f1a2b3c4d5e6",
    "gv01_ai_gen_videos",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
