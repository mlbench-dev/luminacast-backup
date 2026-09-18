"""PR C — product-relevant Pexels queries + multi-angle template.

Covers three behaviours added so a product cast stops pulling brand/location
b-roll (a "Sony storefront" / "Stockholm train") and instead searches for the
actual PRODUCT:

  (a) ``build_pexels_query`` returns a specific→generic ladder built from the
      product name + feature words, with a lifestyle close-up fallback;
  (b) ``_select_video_candidate`` tries the candidate queries in order until
      one returns clips (so a specific phrasing wins, generic still fills);
  (c) ``auto_populate_stock_media`` attaches a multi-angle (close-up +
      hands-using) jump-cut pair to a long product block when the template is
      enabled, and a single clip otherwise.

DB-free: ``engine.cast_generator`` imports without Postgres, and the Pexels
client + OpenRouter call are mocked, so this runs in the CI unit job.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.cast_generator import (
    _extract_feature_words,
    _product_for_block,
    _select_video_candidate,
    auto_populate_stock_media,
    build_pexels_query,
)


# ── (a) build_pexels_query: specific → generic ladder ─────────────────────


def test_build_query_blends_product_name_and_features():
    product = {"name": "Sony WH-1000XM5", "category": "headphones"}
    block = {"key_points": ["noise cancellation", "wireless music"]}
    queries = build_pexels_query(product, block)

    # "WH-1000XM5" has nothing meaningful left after brand/SKU stripping, so
    # the short_name candidate is empty and category + features leads,
    # followed by the lightly-cleaned full name, then the lifestyle fallback.
    assert queries[0].startswith("headphones")
    assert "noise" in queries[0]
    assert queries[1] == "Sony WH-1000XM5"
    assert queries[-1] == "headphones lifestyle close-up"
    assert 2 <= len(queries) <= 3


def test_build_query_never_leaves_brand_token_standing_alone():
    # No category, generic features → the fallback must not be a bare brand.
    product = {"name": "Sony"}
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    assert all(q.lower() != "sony" for q in queries)
    # Fallback collapses an all-brand subject to a neutral "product".
    assert queries[-1] == "product lifestyle close-up"


def test_build_query_does_not_guess_category_from_name_tail():
    """Bug #2 regression: no explicit category must NOT synthesize one by
    grabbing the last surviving word of the product name — that heuristic
    picked "ssd" over "gaming pc" for a real product (see the KOTIN case
    below), because "ssd" happened to be the trailing spec token."""
    product = {
        "name": (
            "KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X "
            "+ 32GB DDR5 + 1TB SSD"
        ),
    }
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    joined = " ".join(queries).lower()
    assert "ssd lifestyle" not in joined
    assert not any(q.strip().lower() == "ssd" for q in queries)


def test_build_query_kotin_case_keeps_pc():
    """Confirmed real-world regression (bugs #1+#2 combined): a blanket
    ≤2-char word cutoff dropped "pc", and the last-word category guess then
    picked "ssd" — producing "prebuilt gaming rtx ryzen" as the primary
    query and an "ssd" fallback, even though Pexels has a large, well-tagged
    "Gaming Pc" category for this exact product."""
    product = {
        "name": (
            "KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X "
            "+ 32GB DDR5 + 1TB SSD"
        ),
    }
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    assert any("pc" in q.lower().split() for q in queries)


def test_build_query_prefers_visual_subject_over_no_category():
    """Bug #5: when the outline LLM supplied visual_subject, it must be used
    in place of the (now-removed) name-derived last-word guess."""
    product = {
        "name": (
            "KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X "
            "+ 32GB DDR5 + 1TB SSD"
        ),
    }
    block = {"key_points": [], "visual_subject": "gaming pc tower"}
    queries = build_pexels_query(product, block)
    assert any(q.startswith("gaming pc tower") for q in queries)
    assert "ssd lifestyle close-up" not in [q.lower() for q in queries]


def test_build_query_includes_lightly_cleaned_full_name():
    """Bug #3: the lightly-cleaned full product name (punctuation normalized,
    no words dropped/reordered, no feature words appended) is tried ahead of
    the generic lifestyle fallback — Pexels' own search handled the full raw
    KOTIN title fine, it doesn't need aggressive trimming."""
    name = (
        "KOTIN G60B Prebuilt Gaming PC — RTX 5070 12GB + Ryzen 7 9700X "
        "+ 32GB DDR5 + 1TB SSD"
    )
    product = {"name": name, "category": "gaming pc"}
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    cleaned = [q for q in queries if "ssd" in q.lower()]
    assert cleaned, "expected the lightly-cleaned full name candidate to survive"
    assert "PC" in cleaned[0] and "5070" in cleaned[0] and "SSD" in cleaned[0]
    assert "—" not in cleaned[0] and "+" not in cleaned[0]


def test_build_query_dedupes_and_caps_at_three():
    product = {"name": "Widget", "category": "widget"}
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    assert len(queries) == len(set(q.lower() for q in queries))
    assert len(queries) <= 3


def test_extract_feature_words_drops_brand_and_filler():
    block = {"key_points": ["The best Sony noise cancellation"], "purpose": "wireless"}
    words = _extract_feature_words(block)
    assert "sony" not in words
    assert "the" not in words
    assert "noise" in words and "cancellation" in words


def test_product_for_block_matches_by_name_then_falls_to_first():
    products = [{"name": "Alpha"}, {"name": "Beta"}]
    assert _product_for_block({"product_name": "Beta"}, products)["name"] == "Beta"
    assert _product_for_block({}, products)["name"] == "Alpha"
    assert _product_for_block({}, []) is None


# ── (b) candidate queries tried specific → generic until one yields ───────


class _SequencedClient:
    """Returns videos only for queries whose text is in ``hits``."""

    def __init__(self, hits: set[str]):
        self.hits = hits
        self.queries: list[str] = []

    async def safe_search_videos(self, query, per_page=3, orientation="portrait", **kw):
        self.queries.append(query)
        if query in self.hits:
            return {"videos": [{"id": 1, "url": f"https://pexels.com/video/{query}/",
                                "width": 1080, "height": 1920, "duration": 8,
                                "image": "https://img/x.jpg",
                                "user": {"name": ""}}]}
        return {"videos": []}


@pytest.mark.asyncio
async def test_select_prefers_specific_query(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    client = _SequencedClient(hits={"specific", "generic"})
    best = await _select_video_candidate(
        client, ["specific", "mid", "generic"], "beat", "portrait", "c", 0,
    )
    # First query already hits → we stop there.
    assert client.queries == ["specific"]
    assert best["url"].endswith("specific/")


@pytest.mark.asyncio
async def test_select_falls_through_to_generic(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    client = _SequencedClient(hits={"generic"})
    best = await _select_video_candidate(
        client, ["specific", "mid", "generic"], "beat", "portrait", "c", 0,
    )
    assert client.queries == ["specific", "mid", "generic"]
    assert best["url"].endswith("generic/")


@pytest.mark.asyncio
async def test_select_returns_none_when_no_query_hits(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    client = _SequencedClient(hits=set())
    best = await _select_video_candidate(
        client, ["a", "b"], "beat", "portrait", "c", 0,
    )
    assert best is None


# ── (c) multi-angle template on a long product block ──────────────────────


class _MultiAngleClient:
    """Every query returns a distinct video keyed by the query string."""

    def __init__(self):
        self.queries: list[str] = []

    async def safe_search_videos(self, query, per_page=3, orientation="portrait", **kw):
        self.queries.append(query)
        vid_id = abs(hash(query)) % 100000
        return {"videos": [{
            "id": vid_id,
            "url": f"https://pexels.com/video/{vid_id}/",
            "width": 1080, "height": 1920, "duration": 8,
            "image": f"https://img/{vid_id}.jpg",
            "user": {"name": ""},
            "video_files": [{"link": f"https://player/{vid_id}.mp4",
                             "file_type": "video/mp4",
                             "width": 1080, "height": 1920}],
        }]}


def _fake_pexels_module(client):
    """Patch services.pexels used inside auto_populate_stock_media."""
    def pick_best_video_file(video):
        return (video.get("video_files") or [{}])[0]
    mod = AsyncMock()
    mod.get_pexels_client_optional = lambda: client
    mod.pick_best_video_file = pick_best_video_file
    return mod


@pytest.mark.asyncio
async def test_multi_angle_attaches_two_clips_on_long_block(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "1")
    monkeypatch.setenv("MULTI_ANGLE_MIN_BLOCK_SEC", "4")
    client = _MultiAngleClient()
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 8,
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    block = out[0]
    assert block.get("multi_angle") is True
    assert len(block["parallel_media"]) == 2
    urls = {c["url"] for c in block["parallel_media"]}
    assert len(urls) == 2
    # Both angle variants were searched.
    assert any("close-up" in q for q in client.queries)
    assert any("hands using" in q for q in client.queries)


@pytest.mark.asyncio
async def test_multi_angle_disabled_keeps_single_clip(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "0")
    client = _MultiAngleClient()
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 8,
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    block = out[0]
    assert not block.get("multi_angle")
    assert len(block["parallel_media"]) == 1


@pytest.mark.asyncio
async def test_multi_angle_skipped_on_short_block(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "1")
    monkeypatch.setenv("MULTI_ANGLE_MIN_BLOCK_SEC", "4")
    client = _MultiAngleClient()
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 3,  # below the 4s threshold
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    assert not out[0].get("multi_angle")
    assert len(out[0]["parallel_media"]) == 1


# ── (d) vision-generated AI queries take priority over text-based ones ────


class _AIQuerySequencedClient:
    """Records every query tried; only ``winning_query`` returns a hit."""

    def __init__(self, winning_query: str):
        self.winning_query = winning_query
        self.queries: list[str] = []

    async def safe_search_videos(self, query, per_page=3, orientation="portrait", **kw):
        self.queries.append(query)
        if query != self.winning_query:
            return {"videos": []}
        return {"videos": [{
            "id": 1, "url": "https://pexels.com/video/1/",
            "width": 1080, "height": 1920, "duration": 8,
            "image": "https://img/1.jpg", "user": {"name": ""},
            "video_files": [{"link": "https://player/1.mp4", "file_type": "video/mp4",
                             "width": 1080, "height": 1920}],
        }]}


@pytest.mark.asyncio
async def test_ai_stock_queries_tried_before_text_based_candidates(monkeypatch):
    """AI-generated queries (from the product's own cover photo, cached on
    Product.ai_stock_queries — see services/product_stock_queries.py) must
    be tried BEFORE build_pexels_query's text-based candidates: a photo
    distinguishes e.g. a saucepan from a fry pan more reliably than parsing
    the product name string."""
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "0")
    client = _AIQuerySequencedClient(winning_query="stainless steel saucepan")
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "cookware demo",
        "estimated_duration_seconds": 8,
    }]
    products = [{
        "name": "Amazon Basics Stainless Steel Saucepan with Lid",
        "category": "cookware",
        "ai_stock_queries": [
            "stainless steel saucepan",
            "cooking saucepan kitchen",
            "sauce simmering on stove",
        ],
    }]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    assert client.queries[0] == "stainless steel saucepan"
    assert out[0]["stock_media_url"] == "https://player/1.mp4"


@pytest.mark.asyncio
async def test_no_ai_stock_queries_falls_back_to_text_based(monkeypatch):
    """A product with no ai_stock_queries (not yet generated, or generation
    failed) must behave exactly as before — build_pexels_query's candidates
    alone, unaffected by this feature's absence."""
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "0")
    client = _AIQuerySequencedClient(winning_query="headphones")
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 8,
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    assert out[0]["stock_media_url"] == "https://player/1.mp4"


@pytest.mark.asyncio
async def test_per_block_ai_query_wins_top_priority_over_product_level(monkeypatch):
    """generate_block_stock_query (the product's cached visual description
    combined with THIS block's own beat) must be tried BEFORE even the
    generic per-product ai_stock_queries — it's the most targeted signal,
    grounded in both the photo and this specific block's content."""
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "0")

    import services.product_stock_queries as psq

    async def _fake_block_query(visual_description, beat_text):
        assert visual_description == "a stainless steel saucepan with a glass lid"
        assert "boiling" in beat_text.lower()
        return "water rapidly boiling pan"
    monkeypatch.setattr(psq, "generate_block_stock_query", _fake_block_query)

    client = _AIQuerySequencedClient(winning_query="water rapidly boiling pan")
    outline = [{
        "category": "avatar_voiceover",
        "render_mode": "voiceover",
        "stock_media_query": "cookware demo",
        "key_points": ["boiling water quickly"],
        "estimated_duration_seconds": 6,
    }]
    products = [{
        "name": "Cooker King Sauce Pan",
        "category": "cookware",
        "ai_stock_queries": ["stainless steel saucepan", "cooking kitchen"],
        "ai_visual_description": "a stainless steel saucepan with a glass lid",
    }]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    assert client.queries[0] == "water rapidly boiling pan"
    assert out[0]["stock_media_url"] == "https://player/1.mp4"


@pytest.mark.asyncio
async def test_no_visual_description_skips_per_block_call(monkeypatch):
    """A product with no ai_visual_description (not yet generated) must not
    attempt the per-block call at all, and behave exactly as the earlier
    ai_stock_queries-only tests — no regression for products without this
    newer field populated yet."""
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "0")

    import services.product_stock_queries as psq
    called = {"n": 0}
    async def _should_not_be_called(visual_description, beat_text):
        called["n"] += 1
        return "should not happen"
    monkeypatch.setattr(psq, "generate_block_stock_query", _should_not_be_called)

    client = _AIQuerySequencedClient(winning_query="stainless steel saucepan")
    outline = [{
        "category": "avatar_voiceover",
        "render_mode": "voiceover",
        "stock_media_query": "cookware demo",
        "estimated_duration_seconds": 6,
    }]
    products = [{
        "name": "Cooker King Sauce Pan",
        "category": "cookware",
        "ai_stock_queries": ["stainless steel saucepan"],
        # no ai_visual_description key at all
    }]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    assert called["n"] == 0
    assert out[0]["stock_media_url"] == "https://player/1.mp4"
