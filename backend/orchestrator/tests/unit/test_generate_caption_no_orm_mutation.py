"""Regression test for the /api/social/generate-caption 500.

Bug: the endpoint did `cast.blocks = blocks_rows` to hand the freshly-queried
blocks to generate_caption() via the ORM relationship attribute. `cast` was
loaded with a bare `db.get(Cast, id)` — no selectinload(Cast.blocks) — so
`cast.blocks` had never been loaded. Assigning to an unloaded SQLAlchemy
relationship triggers an implicit lazy-load of the OLD collection first (to
diff for cascade bookkeeping), and under AsyncSession that lazy load isn't
wrapped in a greenlet context, so it blew up with
``sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called``,
turning "Publish Now" into a 500 every time.

Fix: generate_caption() now takes the blocks list directly instead of
reading it off `cast.blocks` — no ORM relationship mutation involved.
"""
from __future__ import annotations

import asyncio
import sys
import types

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

from services.social_ai import generate_caption


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeVariant:
    def __init__(self, script_text, is_active=True):
        self.script_text = script_text
        self.is_active = is_active


class _FakeBlock:
    def __init__(self, variants):
        self.variants = variants


class _FakeProduct:
    name = "Test Sneaker"
    description = "A comfy shoe"
    price = 49.99
    rating = 4.5


def test_generate_caption_accepts_plain_block_list(monkeypatch):
    """generate_caption must work from a plain list of block-like objects —
    no SQLAlchemy Cast instance or .blocks relationship required."""
    blocks = [
        _FakeBlock([_FakeVariant("Tap the link now.", is_active=True)]),
        _FakeBlock([_FakeVariant("stale inactive line", is_active=False)]),
    ]

    async def _fake_generate_text(**kwargs):
        return '{"caption": "Great shoes!", "hashtags": ["#shoes"], "first_comment": null}'

    fake_service = types.SimpleNamespace(generate_text=_fake_generate_text)
    monkeypatch.setattr(
        "services.openrouter.get_openrouter_service", lambda: fake_service,
    )

    result = _run(generate_caption(blocks, _FakeProduct(), "tiktok"))
    assert result["caption"] == "Great shoes!"
    assert result["hashtags"] == ["shoes"]


def test_generate_caption_handles_empty_blocks(monkeypatch):
    """No blocks (or None) must not raise — script excerpt is just empty."""
    async def _fake_generate_text(**kwargs):
        assert "(no script available)" in kwargs["system_prompt"]
        return '{"caption": "x", "hashtags": [], "first_comment": null}'

    fake_service = types.SimpleNamespace(generate_text=_fake_generate_text)
    monkeypatch.setattr(
        "services.openrouter.get_openrouter_service", lambda: fake_service,
    )

    result = _run(generate_caption(None, _FakeProduct(), "tiktok"))
    assert result["caption"] == "x"


def test_router_does_not_mutate_cast_blocks_relationship():
    """Static guard: routers/social.py must never assign to cast.blocks —
    that's exactly what triggered the MissingGreenlet crash. Passing the
    queried rows straight into generate_caption() sidesteps the ORM
    relationship entirely."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "routers" / "social.py"
    text = src.read_text(encoding="utf-8")
    assert "cast.blocks = " not in text, (
        "must not assign to the cast.blocks ORM relationship — it triggers "
        "an implicit lazy-load that crashes under AsyncSession"
    )
    assert "generate_caption(blocks_rows, product, req.platform)" in text
