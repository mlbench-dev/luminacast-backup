"""Action / motion blocks own their visual — b-roll must never attach.

An avatar_action block's own generated clip is the visual; a full-frame
parallel_media overlay just covers the action. ``block_owns_its_visual``
classifies these, and the guards in routers/casts/{blocks,generation}.py use
it to strip parallel_media on save and on outline→block creation.
"""
from pathlib import Path

import pytest

from utils.block_visual import block_owns_its_visual

_ORCH = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "category,render_mode,expected",
    [
        ("avatar_action", None, True),
        ("avatar_acting", None, True),          # legacy alias
        ("avatar_motion", None, True),          # legacy alias
        ("avatar_body_motion", None, True),     # legacy alias
        (None, "motion", True),
        (None, "body_motion", True),
        ("  Avatar_Action ", None, True),       # trim + case-insensitive
        ("avatar_speaking", None, False),
        ("avatar_voiceover", "avatar_full", False),
        ("pip_talking_head", None, False),
        ("stock_video", None, False),
        ("generated_video", None, False),
        (None, None, False),
        (123, None, False),                    # non-string is safe
    ],
)
def test_block_owns_its_visual(category, render_mode, expected):
    assert block_owns_its_visual(category, render_mode) is expected


def test_bulk_block_save_strips_broll_from_motion_blocks():
    src = (_ORCH / "routers" / "casts" / "blocks.py").read_text()
    assert "from utils.block_visual import block_owns_its_visual" in src
    # the guard runs where parallel_media is persisted
    i = src.index("if parallel_media is not None:")
    j = src.index("block.parallel_media = valid", i)
    guard = src[i:j]
    assert "block_owns_its_visual(" in guard
    assert "parallel_media = []" in guard


def test_outline_to_block_omits_broll_for_motion_scenes():
    src = (_ORCH / "routers" / "casts" / "generation.py").read_text()
    assert "from utils.block_visual import block_owns_its_visual" in src
    assert "if block_owns_its_visual(s.get(\"category\"), s.get(\"render_mode\"))" in src
