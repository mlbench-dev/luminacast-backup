"""Regression tests for ``tasks.cast_render._slot_duration_for_block``.

The function resolves the V1 (video) slot duration for a block so the
post-bake normalize step can hard-trim each baked clip to its slot. A
single video track carries captions (``cap_*``), V1 (``v1_*``), product
overlays (``prod_*``) and parametric motion (``pm_*``) elements — all with
``metadata.block_id`` set. Captions appear first in element order and have
padding on each side, so an over-eager ``metadata.block_id`` match would
return the (shorter) caption slot and leave black tails at block
boundaries (diagnostic render rnd_d198c26461d3).
"""
from __future__ import annotations

from tasks.cast_render import _slot_duration_for_block


def test_slot_duration_matches_v1_not_caption():
    """Regression: caption element's metadata.block_id must NOT match."""
    timeline = {
        "tracks": [{
            "type": "video",
            "elements": [
                # Caption appears FIRST in element order (real production layout)
                {"id": "cap_blk_X", "s": 40.367, "e": 47.400,
                 "metadata": {"block_id": "blk_X"}},
                # V1 element with the canonical id
                {"id": "v1_blk_X", "s": 40.233, "e": 47.500,
                 "metadata": {"block_id": "blk_X"}},
                # Product overlay
                {"id": "prod_blk_X", "s": 40.233, "e": 47.500,
                 "metadata": {"block_id": "blk_X"}},
            ],
        }],
    }
    # MUST return the V1 slot (7.267), not the caption slot (7.033)
    assert abs(_slot_duration_for_block(timeline, "blk_X", 0.0) - 7.267) < 0.001


def test_slot_duration_legacy_v1_id_via_metadata():
    """Fallback: V1-shaped id with metadata.block_id matching still works."""
    timeline = {
        "tracks": [{
            "type": "video",
            "elements": [
                # Suppose a legacy re-export renamed v1_blk_X to v1_xyz
                {"id": "v1_xyz", "s": 40.233, "e": 47.500,
                 "metadata": {"block_id": "blk_X"}},
            ],
        }],
    }
    assert abs(_slot_duration_for_block(timeline, "blk_X", 0.0) - 7.267) < 0.001


def test_slot_duration_caption_only_returns_fallback():
    """If only caption matches via metadata, function should NOT use it."""
    timeline = {
        "tracks": [{
            "type": "video",
            "elements": [
                {"id": "cap_blk_X", "s": 40.367, "e": 47.400,
                 "metadata": {"block_id": "blk_X"}},
            ],
        }],
    }
    # No V1 element → fallback
    assert _slot_duration_for_block(timeline, "blk_X", 9.99) == 9.99
