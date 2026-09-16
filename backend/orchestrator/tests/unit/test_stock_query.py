"""services/stock_query.stockify_query — collapse a shot-style b-roll
description down to its concrete subject for a Pexels search.

Pure unit tests.
"""
import pytest

from services.stock_query import stockify_query


@pytest.mark.parametrize("raw,expected", [
    # the reported case: "flat lay" degrades Pexels to unrelated footage
    ("hoodie flat lay charcoal", "charcoal hoodie"),
    ("hoodie flatlay charcoal", "charcoal hoodie"),
    # style + filler stripped, subject kept
    ("cinematic close-up of person scrolling phone, moody", "person phone"),
    ("minimalist aesthetic product shot", "product"),
    ("4k b-roll footage of a laptop on a desk", "laptop desk"),
    ("slow motion pour of coffee, warm tones", "pour coffee"),
    # already clean — passes through (capped at 4 words)
    ("grey fleece hoodie", "grey fleece hoodie"),
    ("red running shoes on pavement", "red running shoes pavement"),
    # colour floated to the front
    ("sneakers white", "white sneakers"),
    # real subject words that look style-ish are kept
    ("morning coffee by the window", "morning coffee window"),
    # nothing but style words → empty (caller falls back to original)
    ("flat lay", ""),
    ("cinematic aesthetic moody vibes", ""),
    ("", ""),
    (None, ""),
])
def test_stockify(raw, expected):
    assert stockify_query(raw) == expected


def test_dedupes_repeats():
    assert stockify_query("hoodie hoodie grey grey hoodie") == "grey hoodie"


def test_respects_max_words():
    out = stockify_query("red blue green yellow purple orange shoes jacket", max_words=3)
    assert len(out.split()) == 3


def test_never_raises_on_punctuation_only():
    assert stockify_query("!!! ,,, --- ...") == ""


def test_keeps_pc_in_long_spec_heavy_product_name():
    """Bug #4 regression: the old max_words=4 hard cap truncated in original
    word order with no awareness of which words mattered — for the manual
    "Find b-roll" search box on a real product ("KOTIN G60B Prebuilt Gaming
    PC — RTX 5070 12GB + Ryzen 7 9700X + 32GB DDR5 + 1TB SSD") this produced
    "kotin g60b prebuilt gaming", severing "pc" right off the end."""
    raw = "KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X + 32GB DDR5 + 1TB SSD"
    out = stockify_query(raw)
    assert "pc" in out.split()
