"""cast_render must use a block's picked SCENE for the lip-sync face.

The editor timeline bakes the cast's *default* avatar face into every speaking
block's ``v1.props.src``, so ``block.avatar_look_id`` (the scene the user
selects in the Script tab) was ignored at bake time — the rendered video
always showed the default backdrop. The block dispatch now overrides
``face_ref_url`` with the picked look's ``face_ref_key`` when it's a ready
look, taking precedence over the framing-matched talking-head look.

Source assertion (cast_render.py can't be imported without the full stack) —
matches tests/unit/test_action_block_voiceover.py.
"""
from __future__ import annotations

from pathlib import Path

_CAST_RENDER = Path(__file__).resolve().parents[2] / "tasks" / "cast_render.py"


def _dispatch_region() -> str:
    src = _CAST_RENDER.read_text(encoding="utf-8")
    anchor = src.find('Block %s: using picked scene look=%s')
    assert anchor != -1, "per-block scene override log line not found in cast_render.py"
    # grab a window around it for the structural checks
    return src[max(0, anchor - 2000):anchor + 500]


def test_scene_override_reads_block_avatar_look_id():
    region = _dispatch_region()
    assert 'getattr(_sc_blk, "avatar_look_id", None)' in region


def test_scene_override_requires_ready_look_with_image():
    region = _dispatch_region()
    assert '_sc_look.status == "ready"' in region
    assert 'getattr(_sc_look, "face_ref_key", None)' in region


def test_scene_override_skips_action_blocks():
    region = _dispatch_region()
    assert '_sc_blk.category == "avatar_action"' in region
    assert '_sc_blk.render_mode == "body_motion"' in region


def test_scene_override_routes_through_mic_on_resolver():
    region = _dispatch_region()
    assert "resolve_mic_on_face_key(" in region
    assert "face_ref_url = r2.get_public_url(_sc_key)" in region


def test_scene_override_runs_after_talking_head_block():
    """Placement matters — an explicit pick must win over the framing look."""
    src = _CAST_RENDER.read_text(encoding="utf-8")
    th = src.find("Block %s: using talking-head face look=%s")
    sc = src.find("Block %s: using picked scene look=%s")
    assert th != -1 and sc != -1
    assert sc > th, "scene override must come after the talking-head override"
