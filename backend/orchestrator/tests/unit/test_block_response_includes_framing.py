"""Round-6 Bug B follow-up — the cast block API payload must include `framing`.

The validation render found every block returning ``framing=NULL`` in the JSON
API response (the column wasn't serialized). The GET /api/casts/{id} block
serializer in ``routers.casts.get_cast`` now emits a ``framing`` field per
block (default MEDIUM), and the AvatarLook serializer surfaces it too. Frontend
isn't using it yet but it's needed for debug visibility.

The GET serializer is a large inline dict inside an async route that needs a
full DB/R2 harness to invoke, so — following the established pattern in
``test_avatar_look_query_filters_by_framing.py`` — we (1) assert the serializer
source emits the field, and (2) prove the value flows off a real Block model
instance via the exact ``getattr(...) or "MEDIUM"`` expression the serializer
uses.
"""
from __future__ import annotations

from pathlib import Path

ORCH_ROOT = Path(__file__).resolve().parents[2]
CASTS_ROUTER_PATH = ORCH_ROOT / "routers" / "casts.py"
LOOKS_ROUTER_PATH = ORCH_ROOT / "routers" / "avatar_looks.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_block_serializer_source_emits_framing():
    src = _read(CASTS_ROUTER_PATH)
    assert '"framing": getattr(block, "framing", None) or "MEDIUM"' in src, (
        "GET /api/casts/{id} block payload must include the framing field."
    )


def test_avatar_look_serializer_source_emits_framing():
    src = _read(LOOKS_ROUTER_PATH)
    assert '"framing": look.framing or "MEDIUM"' in src, (
        "AvatarLook response payload must include the framing field."
    )


def test_framing_flows_off_a_real_block_instance():
    from models.block import Block, BlockType

    block = Block(
        id="blk_test",
        cast_id="cst_test",
        type=BlockType.PRODUCT,
        category="avatar_speaking",
        position=0,
        framing="CLOSE",
    )
    # Exact expression the GET serializer uses.
    assert (getattr(block, "framing", None) or "MEDIUM") == "CLOSE"


def test_framing_defaults_to_medium_when_unset_on_instance():
    from models.block import Block, BlockType

    # framing not provided → serializer falls back to MEDIUM (the column's
    # server_default is MEDIUM but a freshly-built, unflushed instance has
    # framing=None until persisted, so the serializer's `or "MEDIUM"` matters).
    block = Block(
        id="blk_test2",
        cast_id="cst_test",
        type=BlockType.PRODUCT,
        category="avatar_speaking",
        position=1,
    )
    assert (getattr(block, "framing", None) or "MEDIUM") == "MEDIUM"


def test_block_framing_column_is_non_null_with_medium_default():
    from models.block import Block

    col = Block.__table__.c.framing
    assert col.nullable is False
    assert col.default.arg == "MEDIUM"
