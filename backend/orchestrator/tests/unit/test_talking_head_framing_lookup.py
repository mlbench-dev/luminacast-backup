"""Round-6 Bug B round-3 — talking-head looks give plain lip-sync blocks a
distinct face reference per camera framing.

Before this fix every talk block shared the avatar's default (MEDIUM) look, so
the rendered shots never varied even though each block carried a distinct
``framing``. The fix introduces a reusable per-(avatar, framing) ``talking_head``
AvatarLook and makes both render paths (``generate_cast.py`` preview bake and
``cast_render.py`` finalize bake) prefer it for non-MEDIUM framings.

These tests avoid a live DB — they assert on the prompt helpers, the compiled
SELECT predicates, and the framing-aware resolution source, mirroring the
existing ``test_avatar_look_query_filters_by_framing`` style.
"""
from __future__ import annotations

from pathlib import Path

ORCH_ROOT = Path(__file__).resolve().parents[2]
GENERATE_CAST_PATH = ORCH_ROOT / "tasks" / "generate_cast.py"
CAST_RENDER_PATH = ORCH_ROOT / "tasks" / "cast_render.py"
AVATAR_LOOKS_PATH = ORCH_ROOT / "tasks" / "avatar_looks.py"
CASTS_ROUTER_PATH = ORCH_ROOT / "routers" / "casts" / "generation.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── prompt helpers ────────────────────────────────────────────────────────

def test_talking_head_constants_present():
    from models.avatar_look import (
        TALKING_HEAD_LOOK_TYPE,
        TALKING_HEAD_PROMPT_TEMPLATE,
    )

    assert TALKING_HEAD_LOOK_TYPE == "talking_head"
    # Brand-consistent, product-free, neutral portrait.
    assert "talking-head portrait" in TALKING_HEAD_PROMPT_TEMPLATE
    assert "neutral confident expression" in TALKING_HEAD_PROMPT_TEMPLATE
    assert "product" not in TALKING_HEAD_PROMPT_TEMPLATE.lower()


def test_talking_head_prompt_injects_framing_fragment():
    from models.avatar_look import talking_head_prompt, framing_prompt_fragment

    close = talking_head_prompt("CLOSE")
    wide = talking_head_prompt("WIDE")

    # Each framing's fragment is present and the two prompts differ.
    assert framing_prompt_fragment("CLOSE").rstrip(". ") in close
    assert framing_prompt_fragment("WIDE").rstrip(". ") in wide
    assert close != wide


def test_talking_head_prompt_defaults_to_medium():
    from models.avatar_look import talking_head_prompt, framing_prompt_fragment

    prompt = talking_head_prompt(None)
    assert framing_prompt_fragment("MEDIUM").rstrip(". ") in prompt


def test_talking_head_prompt_appends_style_hint():
    from models.avatar_look import talking_head_prompt

    hinted = talking_head_prompt("CLOSE", style_hint="wearing a navy blazer")
    assert "wearing a navy blazer" in hinted


# ── compiled SELECT predicate ─────────────────────────────────────────────

def test_compiled_talking_head_select_filters_by_framing_and_type():
    from sqlalchemy import select
    from models.avatar_look import AvatarLook, TALKING_HEAD_LOOK_TYPE

    stmt = (
        select(AvatarLook)
        .where(AvatarLook.avatar_id == "avt_123")
        .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
        .where(AvatarLook.framing == "CLOSE")
        .where(AvatarLook.status == "ready")
        .order_by(AvatarLook.created_at.desc())
        .limit(1)
    )
    where_clause = str(stmt).split("WHERE", 1)[1]
    assert "avatar_looks.framing" in where_clause
    assert "avatar_looks.look_type" in where_clause


# ── render-path source assertions ─────────────────────────────────────────

def test_generate_cast_prefers_talking_head_for_non_medium():
    src = _read(GENERATE_CAST_PATH)
    assert "TALKING_HEAD_LOOK_TYPE" in src
    # The lookup is gated on a non-default framing so MEDIUM casts are untouched.
    assert "block_framing != DEFAULT_FRAMING" in src


def test_cast_render_upgrades_face_to_talking_head():
    src = _read(CAST_RENDER_PATH)
    assert "TALKING_HEAD_LOOK_TYPE" in src
    # Action / body_motion blocks must NOT be hijacked by the talk-block path.
    assert "_th_is_action" in src
    assert "_th_framing != DEFAULT_FRAMING" in src


# ── pre-warm wiring ───────────────────────────────────────────────────────

def test_prewarm_helper_enqueues_talking_head_task():
    src = _read(CASTS_ROUTER_PATH)
    assert "generate_talking_head_task" in src
    assert "[talking-head-prewarm]" in src


def test_avatar_looks_task_registered():
    src = _read(AVATAR_LOOKS_PATH)
    assert 'name="tasks.avatar_looks.generate_talking_head"' in src
    assert "def generate_talking_head_task" in src
    assert "def _dispatch_talking_head" in src


# ── round-4: deterministic crops replace image-to-image framing ───────────

def test_talking_head_uses_deterministic_crop_not_i2i():
    """The talking-head dispatch must call the deterministic crop helper and
    apply_framing — NOT route framing through the FLUX/Kontext generation path."""
    src = _read(AVATAR_LOOKS_PATH)
    assert "_generate_talking_head_crop" in src
    assert "from services.framing_crop import apply_framing" in src
    # The talking-head path must no longer fall through to _generate_look_async
    # (the image-to-image generator). Its dispatch ends with the crop helper.
    assert "await _generate_talking_head_crop(look_id)" in src


def test_generate_look_async_no_longer_branches_on_talking_head():
    """Talking-head looks are produced by crops, so _generate_look_async's body
    must not retain a talking_head generation branch."""
    src = _read(AVATAR_LOOKS_PATH)
    assert "elif look_type == TALKING_HEAD_LOOK_TYPE:" not in src


def test_apply_framing_importable_and_distinct_framings_differ():
    """The crop primitive the task relies on yields a distinct bitmap for
    framings that still apply a transform (e.g. WIDE vs MEDIUM), while
    preserving canvas dimensions. CLOSE is intentionally NOT distinct from
    MEDIUM (see services/framing_crop.py's CLOSE branch comment) — the
    in-browser editor preview doesn't apply per-block framing, so CLOSE's
    render-time zoom made the final render look wrongly "cut off" compared
    to the editor. CLOSE now matches MEDIUM (no crop) so preview and render
    agree."""
    import io

    from PIL import Image

    from services.framing_crop import apply_framing

    img = Image.new("RGB", (832, 1488), (30, 60, 120))
    # Add a face-like patch so framings differ on real (non-uniform) content.
    for x in range(360, 472):
        for y in range(440, 560):
            img.putpixel((x, y), (220, 180, 150))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    raw = buf.getvalue()

    close = apply_framing(raw, "CLOSE")
    medium = apply_framing(raw, "MEDIUM")
    wide = apply_framing(raw, "WIDE")
    assert close == medium
    assert wide != medium
    assert Image.open(io.BytesIO(close)).size == (832, 1488)
