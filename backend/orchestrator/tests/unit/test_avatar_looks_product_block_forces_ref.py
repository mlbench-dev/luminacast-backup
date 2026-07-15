"""Bug 2 regression — when an action block IS product-typed (PRODUCT /
PRODUCT_DEMO) or carries an explicit ``product_id``, the action
first/last frame MUST be conditioned on the user's actual product image,
even when the LLM-generated action prompt does not mention the product.

Previously the product reference was gated behind
``_action_prompt_mentions_product``; a non-matching prompt dropped the
reference and FLUX hallucinated a generic prop.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from tasks import avatar_looks as al


# Non-matching prompt: describes a generic action, never names the product
# nor uses an interaction keyword like "holding"/"bottle"/"applying".
NON_MATCHING_PROMPT = "avatar walks toward camera, smiling"


class _FakeProduct:
    id = "prod_1"
    name = "GlowSerum"
    description = "A serum"
    category = "skincare"
    cover_image_url = "https://r2.test/products/glowserum_cover.jpg"


class _FakeBlock:
    def __init__(self, *, block_type=None, product_id=None):
        self.id = "blk_1"
        self.cast_id = "cst_1"
        self.type = block_type
        self.product_id = product_id


class _FakeCast:
    id = "cst_1"
    avatar_id = "avt_1"


class _BlockType:
    """Mimics the BlockType enum members used in production (``.value``)."""

    def __init__(self, value):
        self.value = value


class _FakeSession:
    def __init__(self, block, cast):
        self._block = block
        self._cast = cast

    async def get(self, model, pk):
        name = getattr(model, "__name__", "")
        if name == "Block":
            return self._block
        if name == "Cast":
            return self._cast
        return None

    def add(self, obj):
        self._added = obj

    async def commit(self):
        pass

    async def refresh(self, obj):
        if not getattr(obj, "id", None):
            obj.id = "al_generated_1"


@asynccontextmanager
async def _fake_session_cm(session):
    yield session


def _install_common_mocks(monkeypatch, block):
    cast = _FakeCast()
    session = _FakeSession(block, cast)

    monkeypatch.setattr(al, "_make_session_factory", lambda: (lambda: _fake_session_cm(session)))

    product = _FakeProduct()

    async def _fake_resolve(_session, _block, _cast):
        return product, product.cover_image_url

    monkeypatch.setattr(al, "_resolve_action_product", _fake_resolve)

    captured = {}

    async def _fake_generate(look_id, product_ref_url=None, force_product_emphasis=False):
        captured["look_id"] = look_id
        captured["product_ref_url"] = product_ref_url
        captured["force_product_emphasis"] = force_product_emphasis

    monkeypatch.setattr(al, "_generate_look_async", _fake_generate)

    async def _fake_pin(_block_id, _kind, _look_id):
        pass

    monkeypatch.setattr(al, "_auto_pin_action_look", _fake_pin)

    return captured, product


@pytest.mark.asyncio
async def test_product_typed_block_forces_product_ref(monkeypatch):
    block = _FakeBlock(block_type=_BlockType("PRODUCT"))
    captured, product = _install_common_mocks(monkeypatch, block)

    await al._dispatch_action_frame("blk_1", "start", NON_MATCHING_PROMPT)

    assert captured["product_ref_url"] == product.cover_image_url, (
        "a PRODUCT block must pass the real product image even when the "
        "action prompt doesn't mention the product"
    )
    # Prompt didn't mention the product → emphasis must be forced on.
    assert captured["force_product_emphasis"] is True


@pytest.mark.asyncio
async def test_product_demo_block_forces_product_ref(monkeypatch):
    block = _FakeBlock(block_type=_BlockType("PRODUCT_DEMO"))
    captured, product = _install_common_mocks(monkeypatch, block)

    await al._dispatch_action_frame("blk_1", "end", NON_MATCHING_PROMPT)

    assert captured["product_ref_url"] == product.cover_image_url
    assert captured["force_product_emphasis"] is True


@pytest.mark.asyncio
async def test_block_with_product_id_forces_product_ref(monkeypatch):
    # Generic block type but an explicit product_id attached.
    block = _FakeBlock(block_type=_BlockType("HOOK"), product_id="prod_1")
    captured, product = _install_common_mocks(monkeypatch, block)

    await al._dispatch_action_frame("blk_1", "start", NON_MATCHING_PROMPT)

    assert captured["product_ref_url"] == product.cover_image_url
    assert captured["force_product_emphasis"] is True


@pytest.mark.asyncio
async def test_non_product_block_without_mention_drops_ref(monkeypatch):
    """A non-product block whose prompt doesn't mention the product keeps
    the original heuristic behaviour: no product reference is forced."""
    block = _FakeBlock(block_type=_BlockType("HOOK"), product_id=None)
    captured, _product = _install_common_mocks(monkeypatch, block)

    await al._dispatch_action_frame("blk_1", "start", NON_MATCHING_PROMPT)

    assert captured["product_ref_url"] is None
    assert captured["force_product_emphasis"] is False
