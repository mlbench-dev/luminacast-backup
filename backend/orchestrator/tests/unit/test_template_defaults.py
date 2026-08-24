"""Step 5 — template defaults feed caption / mic into blocks.

Covers ``engine.cast_generator.stamp_template_defaults_on_blocks``: at
generation time, the cast's resolved layout template stamps each block's
``caption_preset`` (cast-level in this schema) and ``mic_on`` flag — but ONLY
when unset. Explicit overrides always win.

A template's ``scene`` field used to also auto-stamp ``Block.background_id``
by name-matching an AvatarBackground row — removed (see
engine.cast_generator.stamp_template_defaults_on_blocks's docstring): it was
invisible/undiscoverable and could silently override a user's explicit
Effects-panel background choice. Existing skip-condition tests below still
assert background_id stays None, since nothing ever sets it now regardless.

DB-free by design (the CI "Backend Tests" unit job runs WITHOUT a Postgres
service): a tiny fake async session answers the one SELECT the helper issues
(load the template by id).
"""
import asyncio

import pytest

from models.block import Block
from models.layout_template import LayoutTemplate


# ── fakes ───────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers the LayoutTemplate-by-id SELECT."""

    def __init__(self, *, template=None):
        self._template = template

    async def flush(self):
        return None

    async def execute(self, statement):
        text = str(statement)
        if "layout_templates" in text:
            rows = [self._template] if self._template is not None else []
            return _FakeResult(rows)
        return _FakeResult([])


class _FakeCast:
    def __init__(self, *, layout_template_id="lt_preset_talking_head",
                 caption_preset=None, avatar_id="ava_1", cast_id="cst_step5"):
        self.id = cast_id
        self.layout_template_id = layout_template_id
        self.caption_preset = caption_preset
        self.avatar_id = avatar_id


def _template(*, caption_preset="hormozi_bold", mic="on", scene="studio"):
    return LayoutTemplate(
        id="lt_preset_talking_head",
        user_id=None,
        name="Talking Head",
        config={
            "content_types": ["tutorial"],
            "face": "full",
            "caption_preset": caption_preset,
            "overlay": {"anchor": "bottom_center", "width_frac": 0.28, "margin": 40},
            "broll": {"mode": "none"},
            "voice": {"mic": mic},
            "scene": scene,
        },
        is_preset=True,
    )


def _block(**kwargs):
    b = Block(id=kwargs.pop("id", "blk_1"))
    b.mic_on = kwargs.pop("mic_on", None)
    b.background_id = kwargs.pop("background_id", None)
    return b


def _stamp(cast, blocks, session):
    from engine.cast_generator import stamp_template_defaults_on_blocks
    asyncio.run(stamp_template_defaults_on_blocks(cast, blocks, session))


# ── caption preset ────────────────────────────────────────────────────────

def test_block_with_no_caption_preset_gets_template_default():
    cast = _FakeCast(caption_preset=None)
    session = _FakeSession(template=_template(caption_preset="hormozi_bold", scene="none"))
    _stamp(cast, [_block()], session)
    assert cast.caption_preset == {"id": "hormozi_bold"}


def test_block_with_explicit_caption_preset_keeps_override():
    cast = _FakeCast(caption_preset={"id": "karaoke_pop", "color": "#fff"})
    session = _FakeSession(template=_template(caption_preset="hormozi_bold", scene="none"))
    _stamp(cast, [_block()], session)
    # Untouched — explicit override wins.
    assert cast.caption_preset == {"id": "karaoke_pop", "color": "#fff"}


# ── mic flag ────────────────────────────────────────────────────────────────

def test_block_mic_unset_gets_template_default_true():
    cast = _FakeCast()
    session = _FakeSession(template=_template(mic="on", scene="none"))
    block = _block(mic_on=None)
    _stamp(cast, [block], session)
    assert block.mic_on is True


def test_block_mic_unset_gets_template_default_false():
    cast = _FakeCast()
    session = _FakeSession(template=_template(mic="off", scene="none"))
    block = _block(mic_on=None)
    _stamp(cast, [block], session)
    assert block.mic_on is False


def test_block_mic_explicitly_set_keeps_override():
    cast = _FakeCast()
    session = _FakeSession(template=_template(mic="on", scene="none"))
    # Template says on, but the block explicitly chose off — override wins.
    block = _block(mic_on=False)
    _stamp(cast, [block], session)
    assert block.mic_on is False


# ── skip conditions ───────────────────────────────────────────────────────

def test_null_layout_template_id_skips_stamping():
    cast = _FakeCast(layout_template_id=None, caption_preset=None)
    session = _FakeSession(template=_template())
    block = _block(mic_on=None, background_id=None)
    _stamp(cast, [block], session)
    assert cast.caption_preset is None
    assert block.mic_on is None
    assert block.background_id is None


def test_feature_flag_off_skips_stamping(monkeypatch):
    monkeypatch.setenv("TEMPLATE_DEFAULTS_ENABLED", "false")
    cast = _FakeCast(caption_preset=None)
    session = _FakeSession(template=_template())
    block = _block(mic_on=None, background_id=None)
    _stamp(cast, [block], session)
    assert cast.caption_preset is None
    assert block.mic_on is None
    assert block.background_id is None


def test_missing_template_row_skips_stamping():
    cast = _FakeCast(caption_preset=None)
    session = _FakeSession(template=None)  # id set but row not found
    block = _block(mic_on=None, background_id=None)
    _stamp(cast, [block], session)
    assert cast.caption_preset is None
    assert block.mic_on is None
