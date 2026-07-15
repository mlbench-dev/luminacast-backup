"""Step 3 — attach a layout template to a cast at generation time.

Covers ``engine.cast_generator.attach_layout_template_to_cast``: the read-only
wiring that stamps ``cast.layout_template_id`` from the Step-2 selector inside
the caller's persisting transaction.

DB-free by design (the CI "Backend Tests" job runs ``tests/unit/`` WITHOUT a
Postgres service): the success path factories the Step-1 presets through the
same tiny fake async session Step 2's test uses, and the failure path patches
``select_layout_template`` to raise so we assert the helper swallows it,
Sentry-captures, and leaves ``layout_template_id = None`` — never blocking
generation.
"""
import asyncio

import pytest

from models.layout_template import LayoutTemplate
from scripts.seed_layout_templates import PRESETS, _preset_id


def _build_preset_rows() -> list[LayoutTemplate]:
    """Factory the 10 Step-1 presets as detached ORM rows (no DB)."""
    return [
        LayoutTemplate(id=_preset_id(slug), user_id=None, name=name,
                       config=config, is_preset=True)
        for slug, name, config in PRESETS
    ]


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
    """Minimal async session mirroring the SELECTs layout_selector issues."""

    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, statement):
        text = str(statement)
        if "= layout_templates.id" in text or "layout_templates.id =" in text:
            wanted = statement.compile().params.get("id_1")
            return _FakeResult([r for r in self._rows if r.id == wanted])
        preset_rows = sorted(
            (r for r in self._rows if r.is_preset), key=lambda r: r.id
        )
        return _FakeResult(preset_rows)


class _FakeCast:
    """Stand-in for the Cast ORM row — only the fields the helper touches."""

    def __init__(self, cast_id="cst_test_step3"):
        self.id = cast_id
        self.layout_template_id = "SENTINEL_UNSET"


def _attach(cast, content_type_id, session):
    from engine.cast_generator import attach_layout_template_to_cast
    asyncio.run(attach_layout_template_to_cast(cast, content_type_id, session))


# ── Success path ──────────────────────────────────────────────────────────

def test_attach_sets_template_id_for_product_showcase():
    cast = _FakeCast()
    _attach(cast, "product_showcase", _FakeSession(_build_preset_rows()))
    # product_showcase is served by spotlight/split_demo/ugc_review; id-asc wins.
    assert cast.layout_template_id == "lt_preset_product_spotlight"


def test_attach_sets_talking_head_for_tutorial():
    cast = _FakeCast()
    _attach(cast, "tutorial", _FakeSession(_build_preset_rows()))
    assert cast.layout_template_id == "lt_preset_talking_head"


def test_attach_unknown_content_type_falls_back_to_talking_head():
    cast = _FakeCast()
    _attach(cast, "nonexistent_xyz", _FakeSession(_build_preset_rows()))
    assert cast.layout_template_id == "lt_preset_talking_head"


def test_attach_none_content_type_falls_back_to_talking_head():
    cast = _FakeCast()
    _attach(cast, None, _FakeSession(_build_preset_rows()))
    assert cast.layout_template_id == "lt_preset_talking_head"


# ── Failure path: selector raises -> id is None, generator continues ────────

def test_attach_selector_raises_leaves_id_none_and_does_not_raise(monkeypatch):
    captured = {}

    async def _boom(content_type_id, db):
        raise RuntimeError("selector exploded")

    def _capture(exc):
        captured["exc"] = exc

    import sentry_sdk
    import services.layout_selector as ls
    monkeypatch.setattr(ls, "select_layout_template", _boom)
    monkeypatch.setattr(sentry_sdk, "capture_exception", _capture)

    cast = _FakeCast()
    # Must NOT raise — generation continues even when selection fails.
    _attach(cast, "product_showcase", _FakeSession(_build_preset_rows()))

    assert cast.layout_template_id is None
    assert isinstance(captured.get("exc"), RuntimeError)


def test_attach_db_error_inside_selector_still_resolves_fallback():
    """If the SELECT itself errors, the selector's own guard returns the
    fallback preset, so the helper still records talking_head (not None)."""

    class _ExplodingSession:
        async def execute(self, statement):
            text = str(statement)
            # Let the fallback-by-id SELECT succeed; fail the load-all SELECT.
            if "= layout_templates.id" in text or "layout_templates.id =" in text:
                wanted = statement.compile().params.get("id_1")
                rows = [r for r in _build_preset_rows() if r.id == wanted]
                return _FakeResult(rows)
            raise RuntimeError("transient DB error on load_presets")

    cast = _FakeCast()
    _attach(cast, "product_showcase", _ExplodingSession())
    assert cast.layout_template_id == "lt_preset_talking_head"
