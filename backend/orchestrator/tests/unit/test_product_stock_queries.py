"""Unit tests for services.product_stock_queries.

Vision-generated Pexels search queries from a product's own cover photo,
cached on Product.ai_stock_queries and layered ahead of (not replacing)
engine.cast_generator.build_pexels_query's text-based candidates. Every
failure path (no image, LLM error, malformed JSON) must return [] rather
than raise, so a caller always has the existing text-based candidates as a
safety net.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

from services import product_stock_queries as psq


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── _parse_searches ─────────────────────────────────────────────────────


def test_parse_searches_plain_json():
    raw = '{"searches": ["stainless steel saucepan", "cooking saucepan kitchen", "sauce simmering on stove"]}'
    assert psq._parse_searches(raw) == [
        "stainless steel saucepan",
        "cooking saucepan kitchen",
        "sauce simmering on stove",
    ]


def test_parse_searches_strips_markdown_fence():
    raw = '```json\n{"searches": ["a", "b", "c"]}\n```'
    assert psq._parse_searches(raw) == ["a", "b", "c"]


def test_parse_searches_dedupes_case_insensitive():
    raw = '{"searches": ["Cordless Massager", "cordless massager", "neck massage"]}'
    assert psq._parse_searches(raw) == ["Cordless Massager", "neck massage"]


def test_parse_searches_caps_at_three():
    raw = '{"searches": ["a", "b", "c", "d", "e"]}'
    assert psq._parse_searches(raw) == ["a", "b", "c"]


def test_parse_searches_missing_key_returns_empty():
    raw = '{"not_searches": ["a", "b"]}'
    assert psq._parse_searches(raw) == []


def test_parse_searches_malformed_json_raises():
    with pytest.raises(Exception):
        psq._parse_searches("not json at all")


# ── generate_ai_stock_queries ───────────────────────────────────────────


def test_generate_ai_stock_queries_happy_path(monkeypatch):
    fake_service = types.SimpleNamespace(
        describe_image=AsyncMock(
            return_value='{"searches": ["stainless steel saucepan", "cooking saucepan kitchen", "sauce simmering on stove"]}'
        ),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_ai_stock_queries("https://cdn/product.jpg"))
    assert result == [
        "stainless steel saucepan",
        "cooking saucepan kitchen",
        "sauce simmering on stove",
    ]
    fake_service.describe_image.assert_awaited_once()


def test_generate_ai_stock_queries_no_url_returns_empty():
    result = _run(psq.generate_ai_stock_queries(""))
    assert result == []


def test_generate_ai_stock_queries_llm_failure_returns_empty_not_raise(monkeypatch):
    fake_service = types.SimpleNamespace(
        describe_image=AsyncMock(side_effect=RuntimeError("upstream down")),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_ai_stock_queries("https://cdn/product.jpg"))
    assert result == []


def test_generate_ai_stock_queries_bad_json_returns_empty_not_raise(monkeypatch):
    fake_service = types.SimpleNamespace(
        describe_image=AsyncMock(return_value="not valid json"),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_ai_stock_queries("https://cdn/product.jpg"))
    assert result == []


# ── get_or_generate_product_ai_stock_queries ────────────────────────────


class _FakeProduct:
    def __init__(self, cover_image_key=None, ai_stock_queries=None, ai_visual_description=None):
        self.cover_image_key = cover_image_key
        self.ai_stock_queries = ai_stock_queries
        self.ai_visual_description = ai_visual_description


class _FakeDB:
    def __init__(self):
        self.added = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def test_returns_cached_without_calling_llm(monkeypatch):
    product = _FakeProduct(cover_image_key="x.jpg", ai_stock_queries=["a", "b"])
    db = _FakeDB()
    r2 = types.SimpleNamespace(get_public_url=lambda k: f"https://cdn/{k}")

    called = {"n": 0}
    async def _should_not_be_called(url):
        called["n"] += 1
        return ["should", "not", "happen"]
    monkeypatch.setattr(psq, "generate_ai_stock_queries", _should_not_be_called)

    result = _run(psq.get_or_generate_product_ai_stock_queries(product, db, r2))
    assert result == ["a", "b"]
    assert called["n"] == 0
    assert not db.committed


def test_no_cover_image_returns_empty_without_calling_llm(monkeypatch):
    product = _FakeProduct(cover_image_key=None, ai_stock_queries=None)
    db = _FakeDB()
    r2 = types.SimpleNamespace(get_public_url=lambda k: f"https://cdn/{k}")

    called = {"n": 0}
    async def _should_not_be_called(url):
        called["n"] += 1
        return []
    monkeypatch.setattr(psq, "generate_ai_stock_queries", _should_not_be_called)

    result = _run(psq.get_or_generate_product_ai_stock_queries(product, db, r2))
    assert result == []
    assert called["n"] == 0


def test_generates_and_persists_on_first_use(monkeypatch):
    product = _FakeProduct(cover_image_key="cover.jpg", ai_stock_queries=None)
    db = _FakeDB()
    r2 = types.SimpleNamespace(get_public_url=lambda k: f"https://cdn/{k}")

    async def _fake_generate(url):
        assert url == "https://cdn/cover.jpg"
        return ["stainless steel saucepan", "cooking saucepan kitchen", "sauce simmering on stove"]
    monkeypatch.setattr(psq, "generate_ai_stock_queries", _fake_generate)

    result = _run(psq.get_or_generate_product_ai_stock_queries(product, db, r2))
    assert result == ["stainless steel saucepan", "cooking saucepan kitchen", "sauce simmering on stove"]
    assert product.ai_stock_queries == result
    assert product in db.added
    assert db.committed


def test_generation_failure_does_not_persist(monkeypatch):
    product = _FakeProduct(cover_image_key="cover.jpg", ai_stock_queries=None)
    db = _FakeDB()
    r2 = types.SimpleNamespace(get_public_url=lambda k: f"https://cdn/{k}")

    async def _fake_generate(url):
        return []
    monkeypatch.setattr(psq, "generate_ai_stock_queries", _fake_generate)

    result = _run(psq.get_or_generate_product_ai_stock_queries(product, db, r2))
    assert result == []
    assert not db.committed
    assert product.ai_stock_queries is None


# ── generate_ai_visual_description / get_or_generate_product_visual_description ──


def test_generate_ai_visual_description_happy_path(monkeypatch):
    fake_service = types.SimpleNamespace(
        describe_image=AsyncMock(
            return_value='"A stainless steel saucepan with a glass lid, used for cooking on a stovetop."'
        ),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_ai_visual_description("https://cdn/product.jpg"))
    assert result == "A stainless steel saucepan with a glass lid, used for cooking on a stovetop."


def test_generate_ai_visual_description_no_url_returns_empty():
    result = _run(psq.generate_ai_visual_description(""))
    assert result == ""


def test_generate_ai_visual_description_llm_failure_returns_empty(monkeypatch):
    fake_service = types.SimpleNamespace(
        describe_image=AsyncMock(side_effect=RuntimeError("upstream down")),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_ai_visual_description("https://cdn/product.jpg"))
    assert result == ""


def test_visual_description_returns_cached_without_calling_llm(monkeypatch):
    product = _FakeProduct(cover_image_key="x.jpg", ai_visual_description="already cached")
    db = _FakeDB()
    r2 = types.SimpleNamespace(get_public_url=lambda k: f"https://cdn/{k}")

    called = {"n": 0}
    async def _should_not_be_called(url):
        called["n"] += 1
        return "should not happen"
    monkeypatch.setattr(psq, "generate_ai_visual_description", _should_not_be_called)

    result = _run(psq.get_or_generate_product_visual_description(product, db, r2))
    assert result == "already cached"
    assert called["n"] == 0
    assert not db.committed


def test_visual_description_generates_and_persists_on_first_use(monkeypatch):
    product = _FakeProduct(cover_image_key="cover.jpg", ai_visual_description=None)
    db = _FakeDB()
    r2 = types.SimpleNamespace(get_public_url=lambda k: f"https://cdn/{k}")

    async def _fake_generate(url):
        assert url == "https://cdn/cover.jpg"
        return "A stainless steel saucepan with a glass lid."
    monkeypatch.setattr(psq, "generate_ai_visual_description", _fake_generate)

    result = _run(psq.get_or_generate_product_visual_description(product, db, r2))
    assert result == "A stainless steel saucepan with a glass lid."
    assert product.ai_visual_description == result
    assert product in db.added
    assert db.committed


# ── generate_block_stock_query ──────────────────────────────────────────


def test_generate_block_stock_query_combines_description_and_beat(monkeypatch):
    fake_service = types.SimpleNamespace(
        generate_text=AsyncMock(return_value="water rapidly boiling pan"),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_block_stock_query(
        "A stainless steel saucepan with a glass lid.",
        "how fast is it boiling on the pan",
    ))
    assert result == "water rapidly boiling pan"
    fake_service.generate_text.assert_awaited_once()
    # Both signals actually reached the LLM call.
    _, kwargs = fake_service.generate_text.await_args
    call_prompt = kwargs.get("prompt") or fake_service.generate_text.await_args.args[0]
    assert "saucepan" in call_prompt
    assert "boiling" in call_prompt


def test_generate_block_stock_query_empty_description_skips_llm_call(monkeypatch):
    called = {"n": 0}
    fake_service = types.SimpleNamespace(
        generate_text=AsyncMock(side_effect=lambda *a, **k: called.__setitem__("n", called["n"] + 1)),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_block_stock_query("", "how fast is it boiling"))
    assert result == ""
    assert called["n"] == 0


def test_generate_block_stock_query_empty_beat_skips_llm_call(monkeypatch):
    called = {"n": 0}
    fake_service = types.SimpleNamespace(
        generate_text=AsyncMock(side_effect=lambda *a, **k: called.__setitem__("n", called["n"] + 1)),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_block_stock_query("a stainless steel saucepan", ""))
    assert result == ""
    assert called["n"] == 0


def test_generate_block_stock_query_llm_failure_returns_empty(monkeypatch):
    fake_service = types.SimpleNamespace(
        generate_text=AsyncMock(side_effect=RuntimeError("upstream down")),
    )
    monkeypatch.setattr(psq, "get_openrouter_service", lambda: fake_service)

    result = _run(psq.generate_block_stock_query("a saucepan", "boiling speed"))
    assert result == ""
