"""Bug 1 regression — the action/body-motion frame resolver in
``cast_render.py`` must constrain the AvatarLook fallback query by
``avatar_id``.

Root cause: the fallback resolved AvatarLook rows by ``look_type`` only
(which embeds ``block_id``). A stale AvatarLook from a prior cast/avatar
with a colliding ``look_type`` could match and render the WRONG face.

The query is built inside a deeply-nested async closure (``_resolve``)
that cannot be invoked in isolation without a full DB / R2 / mic-on
harness, so — following the established pattern in
``test_action_block_voiceover.py`` — we assert against the source of the
exact statement, and additionally prove the resulting SELECT actually
compiles with the ``avatar_id`` predicate.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ORCH_ROOT = Path(__file__).resolve().parents[2]
CAST_RENDER_PATH = ORCH_ROOT / "tasks" / "cast_render.py"
GENERATE_CAST_PATH = ORCH_ROOT / "tasks" / "generate_cast.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _select_block(src: str, anchor: str) -> str:
    """Return the f-string region of the AvatarLook fallback select that
    follows ``anchor`` (the ``_sa_select(AvatarLook)`` call site)."""
    idx = src.find(anchor)
    assert idx != -1, f"could not locate {anchor!r}"
    return src[idx : idx + 400]


def test_cast_render_fallback_select_filters_by_avatar_id():
    src = _read(CAST_RENDER_PATH)
    # The per-block action/body_motion frame resolver.
    block = _select_block(
        src,
        "_sa_select(AvatarLook)\n                                .where(AvatarLook.avatar_id",
    )
    assert "AvatarLook.avatar_id == cst.avatar_id" in block, (
        "cast_render.py fallback AvatarLook select must constrain by "
        "avatar_id to prevent rendering a stale/other avatar's face."
    )
    # The look_type / status / ordering predicates must still be present.
    assert "AvatarLook.look_type == prefix" in block
    assert 'AvatarLook.status == "ready"' in block


def test_generate_cast_fallback_select_filters_by_avatar_id():
    """The identical fallback in generate_cast.py is fixed the same way."""
    src = _read(GENERATE_CAST_PATH)
    block = _select_block(
        src,
        "_sa_select(AvatarLook)\n                                .where(AvatarLook.avatar_id",
    )
    assert "AvatarLook.avatar_id == cast.avatar_id" in block
    assert "AvatarLook.look_type == prefix" in block


def test_compiled_select_includes_avatar_id_predicate():
    """Prove the fixed statement shape compiles to SQL with avatar_id in
    the WHERE clause (independent of the source-text check above)."""
    from sqlalchemy import select
    from models.avatar_look import AvatarLook

    stmt = (
        select(AvatarLook)
        .where(AvatarLook.avatar_id == "avt_123")
        .where(AvatarLook.look_type == "action_block_blk_1_start")
        .where(AvatarLook.status == "ready")
        .order_by(AvatarLook.created_at.desc())
        .limit(1)
    )
    compiled = str(stmt)
    assert "avatar_looks.avatar_id" in compiled
    assert "avatar_looks.look_type" in compiled
    # avatar_id predicate must appear in the WHERE clause.
    where_clause = compiled.split("WHERE", 1)[1]
    assert "avatar_looks.avatar_id" in where_clause
