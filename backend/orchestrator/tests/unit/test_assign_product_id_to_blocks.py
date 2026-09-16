"""Bug regression (cst_d7424cfa4f36) — the cast generator must propagate the
primary product to every block that visually references it, not just PRODUCT
block_type blocks (which PR #163 already handled).

These tests exercise ``assign_product_id_to_blocks`` against lightweight fakes
so no database is required.
"""
from __future__ import annotations

from engine.cast_generator import (
    assign_product_id_to_blocks,
    shorten_stock_query,
)


class _Enum:
    """Mimics a SQLAlchemy Enum member (carries ``.value``)."""

    def __init__(self, value):
        self.value = value


class _Block:
    def __init__(
        self,
        *,
        block_type=None,
        category="avatar_speaking",
        product_id=None,
        body_motion_prompt=None,
        action_start_prompt=None,
        action_end_prompt=None,
        motion_prompt=None,
        key_points=None,
        block_id="blk_x",
    ):
        self.id = block_id
        self.type = _Enum(block_type) if block_type else None
        self.category = category
        self.product_id = product_id
        self.body_motion_prompt = body_motion_prompt
        self.action_start_prompt = action_start_prompt
        self.action_end_prompt = action_end_prompt
        self.motion_prompt = motion_prompt
        self.key_points = key_points


class _Product:
    def __init__(self, pid="prod_38d846993166", name="MASGRE Cordless Neck and Shoulder Massager"):
        self.id = pid
        self.name = name


class _CastProduct:
    def __init__(self, product):
        self.product_id = product.id
        self.product = product


class _Cast:
    def __init__(self, blocks, products):
        self.blocks = blocks
        self.products = products


def _cast_with(blocks, product=True):
    prod = _Product()
    products = [_CastProduct(prod)] if product else []
    return _Cast(blocks, products), prod


# ── Rule 1: categorical (category) ──────────────────────────────────────────

def test_avatar_action_category_gets_product():
    block = _Block(block_type="TRANSITION", category="avatar_action")
    cast, prod = _cast_with([block])
    assert assign_product_id_to_blocks(cast) == 1
    assert block.product_id == prod.id


def test_pip_talking_head_category_gets_product():
    block = _Block(block_type="SOCIAL_PROOF", category="pip_talking_head")
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id


def test_stock_video_category_gets_product():
    block = _Block(block_type="PRODUCT", category="stock_video")
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id


def test_avatar_voiceover_category_gets_product():
    block = _Block(block_type="STORY", category="avatar_voiceover")
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id


# ── Rule 2: categorical (block_type) ────────────────────────────────────────

def test_product_demo_block_type_gets_product():
    # PRODUCT_DEMO with a non-product category still inherits the product.
    block = _Block(block_type="PRODUCT_DEMO", category="avatar_speaking")
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id, "PRODUCT_DEMO must always carry the product"


def test_cta_social_proof_transition_block_types_get_product():
    for bt in ("CTA", "SOCIAL_PROOF", "TRANSITION"):
        block = _Block(block_type=bt, category="avatar_speaking")
        cast, prod = _cast_with([block])
        assign_product_id_to_blocks(cast)
        assert block.product_id == prod.id, f"{bt} must inherit the product"


# ── Rule 3: conservative — pure HOOK/STORY talking heads are skipped ─────────

def test_pure_hook_avatar_speaking_is_skipped():
    block = _Block(block_type="HOOK", category="avatar_speaking")
    cast, _prod = _cast_with([block])
    assert assign_product_id_to_blocks(cast) == 0
    assert block.product_id is None


def test_pure_story_avatar_speaking_is_skipped():
    block = _Block(block_type="STORY", category="avatar_speaking")
    cast, _prod = _cast_with([block])
    assert assign_product_id_to_blocks(cast) == 0
    assert block.product_id is None


# ── Rule 4: bulletproof — prompt text mentions the product ──────────────────

def test_text_detection_overrides_pure_narrative():
    # Pure HOOK/avatar_speaking would normally be skipped, but the prompt names
    # the product → must be assigned unconditionally.
    block = _Block(
        block_type="HOOK",
        category="avatar_speaking",
        action_start_prompt="thumb pressing the control button on the MASGRE massager",
    )
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id


def test_text_detection_via_key_points():
    block = _Block(
        block_type="HOOK",
        category="avatar_speaking",
        key_points=["mention the MASGRE massager benefits"],
    )
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id


def test_text_detection_via_body_motion_prompt():
    block = _Block(
        block_type="STORY",
        category="avatar_speaking",
        body_motion_prompt="pressing the power button on the MASGRE massager",
    )
    cast, prod = _cast_with([block])
    assign_product_id_to_blocks(cast)
    assert block.product_id == prod.id


# ── Guards ──────────────────────────────────────────────────────────────────

def test_no_product_no_assignment():
    block = _Block(block_type="PRODUCT", category="avatar_action")
    cast, _prod = _cast_with([block], product=False)
    assert assign_product_id_to_blocks(cast) == 0
    assert block.product_id is None


def test_existing_product_id_preserved():
    block = _Block(block_type="PRODUCT", category="avatar_action", product_id="prod_other")
    cast, _prod = _cast_with([block])
    assert assign_product_id_to_blocks(cast) == 0
    assert block.product_id == "prod_other"


def test_idempotent_second_run_noop():
    block = _Block(block_type="TRANSITION", category="avatar_action")
    cast, prod = _cast_with([block])
    assert assign_product_id_to_blocks(cast) == 1
    assert block.product_id == prod.id
    # Second run finds nothing new to assign.
    assert assign_product_id_to_blocks(cast) == 0


def test_the_eight_block_cast_from_bug_report():
    """Recreate the cst_d7424cfa4f36 block layout and assert which blocks now
    receive the product. HOOK/STORY plain talking heads stay null; everything
    else (and the user-pain TRANSITION/avatar_action) gets the product."""
    blocks = [
        _Block(block_type="HOOK", category="avatar_speaking", block_id="b0"),
        _Block(block_type="STORY", category="avatar_voiceover", block_id="b1"),
        _Block(block_type="PRODUCT_DEMO", category="avatar_voiceover", block_id="b2"),
        _Block(block_type="PRODUCT", category="avatar_voiceover", product_id="prod_38d846993166", block_id="b3"),
        _Block(block_type="PRODUCT", category="pip_talking_head", product_id="prod_38d846993166", block_id="b4"),
        _Block(block_type="PRODUCT", category="stock_video", product_id="prod_38d846993166", block_id="b5"),
        _Block(block_type="SOCIAL_PROOF", category="pip_talking_head", block_id="b6"),
        _Block(
            block_type="TRANSITION",
            category="avatar_action",
            action_end_prompt="the product is on the neck",
            block_id="b7",
        ),
        _Block(block_type="CTA", category="avatar_speaking", product_id="prod_38d846993166", block_id="b8"),
    ]
    cast, prod = _cast_with(blocks)
    assign_product_id_to_blocks(cast)

    by_id = {b.id: b for b in blocks}
    # HOOK plain talking head — stays null.
    assert by_id["b0"].product_id is None
    # STORY avatar_voiceover — voiceover category → assigned.
    assert by_id["b1"].product_id == prod.id
    # PRODUCT_DEMO — the headline bug → assigned.
    assert by_id["b2"].product_id == prod.id
    # SOCIAL_PROOF pip_talking_head — assigned.
    assert by_id["b6"].product_id == prod.id
    # TRANSITION avatar_action (user pain) — assigned.
    assert by_id["b7"].product_id == prod.id


# ── Fix 4: shortened stock query ────────────────────────────────────────────

def test_shorten_stock_query_strips_brand_and_filler():
    out = shorten_stock_query("MASGRE Cordless Neck and Shoulder Massager with Heat")
    assert "massager" in out
    assert "neck" in out
    assert "shoulder" in out
    assert "masgre" not in out.lower(), "brand token must be dropped"
    assert "with" not in out.split(), "filler tokens must be dropped"
    assert len(out) < 40


def test_shorten_stock_query_strips_sku():
    out = shorten_stock_query("Sony WH-1000XM5 Wireless Headphones")
    assert "headphones" in out
    assert "wh" not in out.split() and "wh-1000xm5" not in out
    assert "sony" not in out.split()


def test_shorten_stock_query_empty():
    assert shorten_stock_query("") == ""
    assert shorten_stock_query("   ") == ""


def test_shorten_stock_query_keeps_short_product_type_words():
    """Bug #1 regression: a blanket ≤2-char length cutoff used to drop real
    product-type words like "pc"/"tv", not just filler/units — for a real
    product ("KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X
    + 32GB DDR5 + 1TB SSD") this produced "prebuilt gaming rtx ryzen" with
    "pc" silently dropped, even though Pexels has a large, well-tagged
    "Gaming Pc" category for exactly this product."""
    out = shorten_stock_query(
        "KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X "
        "+ 32GB DDR5 + 1TB SSD"
    )
    assert "pc" in out.split()


def test_shorten_stock_query_still_drops_short_filler():
    """Short words that AREN'T real product-type nouns keep getting dropped —
    the fix is a whitelist, not a blanket length-cutoff removal."""
    out = shorten_stock_query("Sony WH-1000XM5 Wireless Headphones")
    assert "wh" not in out.split()
