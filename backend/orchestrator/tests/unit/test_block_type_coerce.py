"""Bug 2 regression — ``BlockType.coerce`` must map the lowercase block_type
strings the outline LLM emits onto the upper-case enum values.

The legacy outline endpoint did ``BlockType(raw)`` on the raw LLM value; the
enum values are upper-case so every lowercase value raised ValueError and the
caller's ``except`` collapsed the whole cast to PRODUCT (cst_0f43a80b624d had
ONLY type=PRODUCT). ``coerce`` upper-cases + synonym-maps first.
"""
from __future__ import annotations

import pytest

from models.block import BlockType


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("product", BlockType.PRODUCT),
        ("hook", BlockType.HOOK),
        ("story", BlockType.STORY),
        ("cta", BlockType.CTA),
        ("intro", BlockType.INTRO),
        ("closing", BlockType.CLOSING),
        ("flash_sale", BlockType.FLASH_SALE),
        ("social_proof", BlockType.SOCIAL_PROOF),
        ("product_demo", BlockType.PRODUCT_DEMO),
        ("transition", BlockType.TRANSITION),
        # already upper-case / already an enum
        ("PRODUCT", BlockType.PRODUCT),
        (BlockType.HOOK, BlockType.HOOK),
        # friendly synonyms
        ("call_to_action", BlockType.CTA),
        ("outro", BlockType.CLOSING),
        ("demo", BlockType.PRODUCT_DEMO),
    ],
)
def test_coerce_maps_known_values(raw, expected):
    assert BlockType.coerce(raw) is expected


@pytest.mark.parametrize("raw", [None, "", "   ", "not_a_real_type", 123, []])
def test_coerce_falls_back_to_product(raw):
    assert BlockType.coerce(raw) is BlockType.PRODUCT


def test_coerce_respects_custom_default():
    assert BlockType.coerce("garbage", default=BlockType.HOOK) is BlockType.HOOK


def test_coerce_preserves_type_variety():
    """Given the lowercase mix the LLM produces, coercion yields several
    distinct block types rather than collapsing everything to PRODUCT."""
    raw_types = [
        "hook", "story", "product", "product_demo",
        "social_proof", "transition", "cta",
    ]
    coerced = {BlockType.coerce(t) for t in raw_types}
    assert len(coerced) >= 4
    assert BlockType.HOOK in coerced
    assert BlockType.CTA in coerced
