"""PR-I: document polluted pre-PR-I video-second usage rows (no schema change)

Revision ID: pri01_usage_vid_sec_note
Revises: ct01_usage_events
Create Date: 2026-06-13

This migration is intentionally EMPTY — it changes no schema and rewrites no
rows. It exists only to document a known data-quality issue for anyone building
historical cost rollups off `usage_events`.

Background (PR-I, GitHub PR #116)
---------------------------------
Before PR-I, `tasks/cast_render.py::_log_render_usage` only computed a correct
video-second quantity and a non-zero provider cost for the `fal_t2v` / `fal_i2v`
backends. The other video-second backends — `fal_hallo`,
`fal_sync_lipsync_v2_pro`, `fal_sync_lipsync_v3`, `product_elements_bake`
(Kling v3 Pro Elements) and `wavespeed` — all map to
`quantity_unit = "video_seconds"` but fell through to the GPU branch:

    cost     = calculate_gpu_render_cost("fal_ai"/"wavespeed", elapsed_seconds)  # → 0.0
    quantity = elapsed_seconds   # wall-clock seconds, NOT seconds of output video

`calculate_gpu_render_cost` has no rate-table entry for providers `fal_ai` or
`wavespeed`, so it returned $0.00. The result: avatar/PIP/action render rows in
`usage_events` recorded wall-clock GPU/network seconds (observed 400–1100s) under
a `video_seconds` label, at `provider_cost_usd = 0` and `user_price_usd = 0`.

Polluted rows (exclude from historical cost rollups)
----------------------------------------------------
Rows created BEFORE PR-I matching ALL of the following are polluted and must be
excluded from any historical cost / margin analysis. Do NOT trust their
`quantity` or `*_usd` columns:

    SELECT *
    FROM usage_events
    WHERE event_type IN ('avatar_render', 'pip_render', 'action_render')
      AND provider     IN ('fal_ai', 'wavespeed')
      AND quantity_unit = 'video_seconds'
      AND quantity      > 60          -- an avatar/PIP/action clip is ~5–15s of
                                      -- output video; >60 here is wall-clock time
      AND created_at    < '<PR-I deploy timestamp>';

After PR-I these backends bill the OUTPUT clip length (slot duration, typically
5–15s) at their per-second provider rate, so quantities of 5–15 with non-zero
cost are correct going forward.

We deliberately do NOT rewrite the historical rows here: backfilling the true
output seconds would require re-deriving slot durations per block from timeline
snapshots that are not all retained. Recomputation is out of scope for PR-I
(see the PR description). Treat the pre-PR-I rows as unrecoverable and filter
them out at the reporting layer instead.
"""
from typing import Sequence, Union
from alembic import op  # noqa: F401  (kept for parity with other migrations)
import sqlalchemy as sa  # noqa: F401


revision: str = "pri01_usage_vid_sec_note"
down_revision: Union[str, None] = "ct01_usage_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty — documentation-only migration. See module docstring.
    pass


def downgrade() -> None:
    # Nothing to undo — this migration never altered schema or data.
    pass
