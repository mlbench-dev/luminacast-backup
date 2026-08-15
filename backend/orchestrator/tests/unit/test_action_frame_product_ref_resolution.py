"""Fix 2 regression — the action-frame regen route must condition the rendered
frame on the chosen product image.

- When the block already carries ``product_id`` → that product's image is used.
- When it does not but the cast has a primary product AND the prompt / scene
  text references the product → fall back to the primary product, persist
  ``block.product_id`` so future regens skip the fallback, and use its image.
- Otherwise → no product reference.

These tests drive ``_resolve_action_frame_product_ref`` with fakes so no
database is required.
"""
from __future__ import annotations

import pytest

import routers.casts.frames as casts


class _Product:
    def __init__(self, pid, name, cover="cover.jpg"):
        self.id = pid
        self.name = name
        self.cover_image_key = cover
        self.media_keys = None


class _CastProduct:
    def __init__(self, product, position=0):
        self.product_id = product.id
        self.product = product
        self.position = position


class _Block:
    def __init__(self, *, product_id=None, action_start_prompt=None, action_end_prompt=None,
                 body_motion_prompt=None, framing="MEDIUM"):
        self.id = "blk_f9218b59d823"
        self.cast_id = "cst_d7424cfa4f36"
        self.product_id = product_id
        self.action_start_prompt = action_start_prompt
        self.action_end_prompt = action_end_prompt
        self.body_motion_prompt = body_motion_prompt
        self.framing = framing


class _Cast:
    id = "cst_d7424cfa4f36"


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _ExecResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _ScalarResult(self._value)


class _FakeDB:
    """Minimal async DB: ``get`` resolves products by id; ``execute`` returns
    the first CastProduct."""

    def __init__(self, products_by_id, cast_product=None):
        self._products = products_by_id
        self._cp = cast_product

    async def get(self, model, pk):
        return self._products.get(pk)

    async def execute(self, _query):
        return _ExecResult(self._cp)


@pytest.fixture(autouse=True)
def _stub_r2(monkeypatch):
    class _R2:
        def get_public_url(self, key):
            return f"https://r2.test/{key}"

    monkeypatch.setattr(
        "services.r2_storage.get_r2_storage_service", lambda: _R2()
    )


@pytest.mark.asyncio
async def test_block_with_product_id_uses_that_product():
    prod = _Product("prod_38d846993166", "MASGRE Massager")
    db = _FakeDB({prod.id: prod})
    block = _Block(product_id=prod.id)
    cast = _Cast()

    url, name, pid = await casts._resolve_action_frame_product_ref(
        db, block, cast, "any prompt"
    )
    assert url == "https://r2.test/cover.jpg"
    assert name == "MASGRE Massager"
    assert pid == prod.id


@pytest.mark.asyncio
async def test_fallback_to_primary_when_prompt_mentions_product():
    prod = _Product("prod_38d846993166", "MASGRE Cordless Neck Massager")
    cp = _CastProduct(prod)
    db = _FakeDB({prod.id: prod}, cast_product=cp)
    block = _Block(product_id=None)  # not linked
    cast = _Cast()

    url, name, pid = await casts._resolve_action_frame_product_ref(
        db, block, cast,
        "thumb pressing the control button on the MASGRE massager",
    )
    assert url == "https://r2.test/cover.jpg"
    assert pid == prod.id
    # Fallback must be persisted on the block so future regens skip it.
    assert block.product_id == prod.id


@pytest.mark.asyncio
async def test_fallback_when_scene_text_says_the_product():
    prod = _Product("prod_38d846993166", "MASGRE Massager")
    cp = _CastProduct(prod)
    db = _FakeDB({prod.id: prod}, cast_product=cp)
    # The regen prompt itself is generic, but the persisted user edit on the
    # block says "the product is on the neck".
    block = _Block(product_id=None, action_end_prompt="the product is on the neck")
    cast = _Cast()

    url, name, pid = await casts._resolve_action_frame_product_ref(
        db, block, cast, "avatar adjusts pose, smiling",
    )
    assert pid == prod.id
    assert block.product_id == prod.id


@pytest.mark.asyncio
async def test_no_fallback_when_no_reference():
    prod = _Product("prod_38d846993166", "MASGRE Massager")
    cp = _CastProduct(prod)
    db = _FakeDB({prod.id: prod}, cast_product=cp)
    block = _Block(product_id=None)  # generic, no mention anywhere
    cast = _Cast()

    url, name, pid = await casts._resolve_action_frame_product_ref(
        db, block, cast, "avatar walks toward camera, smiling",
    )
    assert url is None
    assert pid is None
    assert block.product_id is None


@pytest.mark.asyncio
async def test_no_product_on_cast_returns_none():
    db = _FakeDB({}, cast_product=None)
    block = _Block(product_id=None, action_start_prompt="holding the MASGRE massager")
    cast = _Cast()

    url, name, pid = await casts._resolve_action_frame_product_ref(
        db, block, cast, "holding the MASGRE massager",
    )
    assert url is None and name == "" and pid is None
