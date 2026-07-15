"""Round-6 Bug B — the per-block AvatarLook fallback query must constrain by
``framing`` so a look generated for one framing is NEVER reused for another.

The query lives inside a deeply-nested async closure (``_resolve``) that can't
be invoked in isolation, so — following the established pattern in
``test_cast_render_avatar_look_avatar_id_filter.py`` — we assert against the
source of the exact statement and prove the SELECT compiles with the
``framing`` predicate in its WHERE clause.
"""
from __future__ import annotations

from pathlib import Path

ORCH_ROOT = Path(__file__).resolve().parents[2]
CAST_RENDER_PATH = ORCH_ROOT / "tasks" / "cast_render.py"
GENERATE_CAST_PATH = ORCH_ROOT / "tasks" / "generate_cast.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cast_render_fallback_select_filters_by_framing():
    src = _read(CAST_RENDER_PATH)
    assert ".where(AvatarLook.framing == block_framing)" in src
    # The block_framing is derived from the block, defaulting to MEDIUM.
    assert 'getattr(blk, "framing", None) or "MEDIUM"' in src


def test_generate_cast_fallback_select_filters_by_framing():
    src = _read(GENERATE_CAST_PATH)
    assert ".where(AvatarLook.framing == block_framing)" in src
    assert 'getattr(block, "framing", None) or "MEDIUM"' in src


def test_compiled_select_includes_framing_predicate():
    from sqlalchemy import select
    from models.avatar_look import AvatarLook

    stmt = (
        select(AvatarLook)
        .where(AvatarLook.avatar_id == "avt_123")
        .where(AvatarLook.look_type == "action_block_blk_1_start")
        .where(AvatarLook.framing == "CLOSE")
        .where(AvatarLook.status == "ready")
        .order_by(AvatarLook.created_at.desc())
        .limit(1)
    )
    compiled = str(stmt)
    where_clause = compiled.split("WHERE", 1)[1]
    assert "avatar_looks.framing" in where_clause


def test_framing_column_default_is_medium():
    from models.avatar_look import AvatarLook

    col = AvatarLook.__table__.c.framing
    assert col.default.arg == "MEDIUM"
    assert col.nullable is False
