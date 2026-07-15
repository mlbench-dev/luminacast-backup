"""regr-wiring: bind the avatar's mic_on_* look to mic-on blocks.

Covers ``engine.cast_generator.bind_mic_on_look_to_blocks`` (and its helper
``_resolve_ready_mic_on_look_id``): at outline-persist time, every block with
``mic_on == True`` and an unset ``avatar_look_id`` is bound to the avatar's
ready ``mic_on_*`` look so the renderer bakes the clip-on lavalier variant.

Contract:
  * resolver returns the latest ready mic_on_* look id (original/default
    preferred); returns None when the avatar has no ready mic_on_* look;
  * binder assigns that id to mic-on blocks with NULL avatar_look_id only —
    blocks that already have a look, or are mic-off, are left untouched;
  * no ready look → blocks left NULL (render falls back to base look);
  * the feature flag gates the whole thing off.

DB-free: a tiny fake async session answers the single avatar_looks SELECT.
"""
import asyncio
import importlib

import pytest


# ── fakes ───────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Answers the avatar_looks SELECT with the rows it was given (already in
    the order the query would return)."""

    def __init__(self, *, looks=None):
        self._looks = list(looks or [])

    async def flush(self):
        return None

    async def execute(self, statement):
        text = str(statement)
        if "avatar_looks" in text:
            return _FakeResult(self._looks)
        return _FakeResult([])


class _Look:
    def __init__(self, look_id):
        self.id = look_id


class _Cast:
    def __init__(self, *, avatar_id="ava_1", default_avatar_look_id="al_base",
                 cast_id="cst_wiring"):
        self.id = cast_id
        self.avatar_id = avatar_id
        self.default_avatar_look_id = default_avatar_look_id


class _Block:
    def __init__(self, *, block_id="blk_1", mic_on=None, avatar_look_id=None):
        self.id = block_id
        self.mic_on = mic_on
        self.avatar_look_id = avatar_look_id


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("MIC_ON_LOOK_VARIANT_ENABLED", "true")
    yield


def _bind(cast, blocks, session):
    from engine.cast_generator import bind_mic_on_look_to_blocks
    asyncio.run(bind_mic_on_look_to_blocks(cast, blocks, session))


def _resolve(avatar_id, session):
    from engine.cast_generator import _resolve_ready_mic_on_look_id
    return asyncio.run(_resolve_ready_mic_on_look_id(avatar_id, session))


# ── resolver ────────────────────────────────────────────────────────────────

def test_resolver_returns_latest_ready_mic_on_look():
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    assert _resolve("ava_1", session) == "al_mic_ready"


def test_resolver_returns_none_when_no_ready_look():
    session = _FakeSession(looks=[])
    assert _resolve("ava_1", session) is None


def test_resolver_returns_none_for_missing_avatar_id():
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    assert _resolve(None, session) is None


# ── binder ────────────────────────────────────────────────────────────────

def test_mic_on_block_gets_look_bound():
    cast = _Cast()
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    block = _Block(mic_on=True, avatar_look_id=None)
    _bind(cast, [block], session)
    assert block.avatar_look_id == "al_mic_ready"


def test_mic_off_block_left_untouched():
    cast = _Cast()
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    block = _Block(mic_on=False, avatar_look_id=None)
    _bind(cast, [block], session)
    assert block.avatar_look_id is None


def test_mic_none_block_left_untouched():
    cast = _Cast()
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    block = _Block(mic_on=None, avatar_look_id=None)
    _bind(cast, [block], session)
    assert block.avatar_look_id is None


def test_existing_avatar_look_id_is_not_overwritten():
    cast = _Cast()
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    block = _Block(mic_on=True, avatar_look_id="al_user_choice")
    _bind(cast, [block], session)
    # Explicit per-block look wins — binder only fills NULLs.
    assert block.avatar_look_id == "al_user_choice"


def test_no_ready_look_leaves_block_null():
    cast = _Cast()
    session = _FakeSession(looks=[])
    block = _Block(mic_on=True, avatar_look_id=None)
    _bind(cast, [block], session)
    # Render falls back to the base look; resolver lazy-generates later.
    assert block.avatar_look_id is None


def test_feature_flag_off_skips_binding(monkeypatch):
    monkeypatch.setenv("MIC_ON_LOOK_VARIANT_ENABLED", "false")
    cast = _Cast()
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    block = _Block(mic_on=True, avatar_look_id=None)
    _bind(cast, [block], session)
    assert block.avatar_look_id is None


def test_binds_multiple_mic_on_blocks():
    cast = _Cast()
    session = _FakeSession(looks=[_Look("al_mic_ready")])
    blocks = [
        _Block(block_id="blk_1", mic_on=True, avatar_look_id=None),
        _Block(block_id="blk_2", mic_on=True, avatar_look_id=None),
        _Block(block_id="blk_3", mic_on=False, avatar_look_id=None),
    ]
    _bind(cast, blocks, session)
    assert blocks[0].avatar_look_id == "al_mic_ready"
    assert blocks[1].avatar_look_id == "al_mic_ready"
    assert blocks[2].avatar_look_id is None
