"""PUT /casts/{id}/blocks/{id} — assigning ``avatar_look_id`` must accept a
scene that is still generating.

The Script tab optimistically pre-selects an in-flight look (so it appears the
moment it's ready). The endpoint used to hard-reject anything with
``status != "ready"`` — producing a "Failed to update background / Look is not
ready" toast right next to "Scene generation started". Now only a hard-failed
look is rejected; the renderer already falls back to the clean face for a look
that isn't ready at bake time.

DB-free: source assertion, matching tests/unit/test_action_block_voiceover.py.
"""
from __future__ import annotations

from pathlib import Path

_BLOCKS = Path(__file__).resolve().parents[2] / "routers" / "casts" / "blocks.py"


def _update_block_body() -> str:
    src = _BLOCKS.read_text(encoding="utf-8")
    start = src.find("async def update_block(")
    assert start != -1
    end = src.find("\n@router", start)
    return src[start:end if end != -1 else None]


def test_in_flight_look_is_accepted_only_failed_rejected():
    body = _update_block_body()
    # The old blanket guard is gone...
    assert 'if look.status != "ready":\n                raise HTTPException(400, "Look is not ready")' not in body
    # ...replaced by a failed-only reject for the background look.
    assert 'if look.status == "failed":' in body
    assert "failed to generate" in body.lower()


def test_missing_look_still_404s():
    body = _update_block_body()
    assert 'raise HTTPException(404, "Look not found")' in body
