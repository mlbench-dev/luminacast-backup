"""Round-6 Bug B follow-up — finalize pre-warms missing framing looks.

Before this fix, an avatar_action block requesting framing=CLOSE with no CLOSE
look on file fell back to a wrong-framing shared look (every block reused the
backfilled MEDIUM look al_…). ``_ensure_framings_ready`` now scans the action /
body_motion blocks at finalize time and ENQUEUES a fresh look-gen task for any
block whose framing has no matching READY look, so the shots actually vary.

This test drives ``_ensure_framings_ready`` directly with a fake async session
and asserts a look-gen task is enqueued when the CLOSE look is missing, and is
NOT enqueued when a matching READY look already exists.
"""
from __future__ import annotations

import types

import pytest

from routers.casts import render as casts_router


class _FakeBlock:
    def __init__(self, **kw):
        self.id = kw.get("id", "blk_1")
        self.cast_id = kw.get("cast_id", "cst_1")
        self.category = kw.get("category", "avatar_action")
        self.render_mode = kw.get("render_mode", "body_motion")
        self.framing = kw.get("framing", "CLOSE")
        self.action_start_prompt = kw.get("action_start_prompt", "avatar walks into a sunny kitchen")
        self.action_end_prompt = kw.get("action_end_prompt", "avatar smiles holding the product")
        self.body_motion_start_prompt = kw.get("body_motion_start_prompt")
        self.body_motion_end_prompt = kw.get("body_motion_end_prompt")
        self.deleted_at = kw.get("deleted_at", None)


class _FakeCast:
    def __init__(self, avatar_id="avt_1", id="cst_1"):
        self.avatar_id = avatar_id
        self.id = id


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal async session: ``execute`` returns the block list,
    ``scalar`` returns whatever the configured look-lookup yields."""

    def __init__(self, blocks, ready_look_id=None):
        self._blocks = blocks
        self._ready_look_id = ready_look_id

    async def execute(self, _stmt):
        return _ScalarsResult(self._blocks)

    async def scalar(self, _stmt):
        # Models the "does a READY look exist for this framing?" query.
        return self._ready_look_id


@pytest.fixture
def captured_tasks(monkeypatch):
    """Replace the frame-gen Celery tasks with capturing stubs."""
    calls = {"action": [], "body_motion": [], "talking_head": []}

    action_stub = types.SimpleNamespace(
        delay=lambda block_id, kind, prompt: calls["action"].append((block_id, kind, prompt))
    )
    body_stub = types.SimpleNamespace(
        delay=lambda block_id, kind, prompt: calls["body_motion"].append((block_id, kind, prompt))
    )
    talking_head_stub = types.SimpleNamespace(
        delay=lambda avatar_id, framing: calls["talking_head"].append((avatar_id, framing))
    )

    import tasks.avatar_looks as al
    monkeypatch.setattr(al, "generate_action_frame_task", action_stub, raising=True)
    monkeypatch.setattr(al, "generate_body_motion_frame_task", body_stub, raising=True)
    monkeypatch.setattr(al, "generate_talking_head_task", talking_head_stub, raising=True)
    return calls


@pytest.mark.asyncio
async def test_look_gen_enqueued_when_close_look_missing(captured_tasks):
    block = _FakeBlock(framing="CLOSE")
    session = _FakeSession([block], ready_look_id=None)  # no matching look

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    # start + end → two action frame tasks enqueued.
    assert enqueued == 2
    assert len(captured_tasks["action"]) == 2
    assert {c[1] for c in captured_tasks["action"]} == {"start", "end"}
    assert all(c[0] == block.id for c in captured_tasks["action"])


@pytest.mark.asyncio
async def test_no_look_gen_when_ready_look_exists(captured_tasks):
    block = _FakeBlock(framing="CLOSE")
    # A ready look for this framing already exists → nothing to enqueue.
    session = _FakeSession([block], ready_look_id="al_existing")

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    assert enqueued == 0
    assert captured_tasks["action"] == []
    assert captured_tasks["body_motion"] == []


@pytest.mark.asyncio
async def test_talk_block_at_medium_enqueues_nothing(captured_tasks):
    # A plain lip-sync block at the default MEDIUM framing keeps the avatar's
    # default look — no talking-head pre-warm needed.
    block = _FakeBlock(
        category="avatar_speaking", render_mode="avatar_full", framing="MEDIUM",
    )
    session = _FakeSession([block], ready_look_id=None)

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    assert enqueued == 0
    assert captured_tasks["action"] == []
    assert captured_tasks["talking_head"] == []


@pytest.mark.asyncio
async def test_talk_block_non_medium_enqueues_talking_head(captured_tasks):
    # A plain lip-sync block at a variety framing pre-warms a reusable
    # talking-head look keyed by (avatar_id, framing), NOT by block.
    block = _FakeBlock(
        category="avatar_speaking", render_mode="avatar_full", framing="CLOSE",
    )
    session = _FakeSession([block], ready_look_id=None)

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    assert enqueued == 1
    assert captured_tasks["action"] == []
    assert captured_tasks["talking_head"] == [("avt_1", "CLOSE")]


@pytest.mark.asyncio
async def test_talk_blocks_same_framing_enqueue_once(captured_tasks):
    # Two talk blocks at the same framing collapse to a single gen task.
    blocks = [
        _FakeBlock(id="blk_a", category="avatar_speaking", render_mode="avatar_full", framing="CLOSE"),
        _FakeBlock(id="blk_b", category="avatar_speaking", render_mode="avatar_full", framing="CLOSE"),
    ]
    session = _FakeSession(blocks, ready_look_id=None)

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    assert enqueued == 1
    assert captured_tasks["talking_head"] == [("avt_1", "CLOSE")]


@pytest.mark.asyncio
async def test_talk_block_skips_when_ready_talking_head_exists(captured_tasks):
    block = _FakeBlock(
        category="avatar_speaking", render_mode="avatar_full", framing="CLOSE",
    )
    session = _FakeSession([block], ready_look_id="al_existing_th")

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    assert enqueued == 0
    assert captured_tasks["talking_head"] == []


@pytest.mark.asyncio
async def test_body_motion_only_block_uses_body_motion_task(captured_tasks):
    # No action_*_prompt, only body_motion_*_prompt → body-motion task path.
    block = _FakeBlock(
        category="avatar_action",
        render_mode="body_motion",
        framing="WIDE",
        action_start_prompt=None,
        action_end_prompt=None,
        body_motion_start_prompt="avatar starts a fashion walk",
        body_motion_end_prompt="avatar poses at the end of the runway",
    )
    session = _FakeSession([block], ready_look_id=None)

    enqueued = await casts_router._ensure_framings_ready(session, _FakeCast())

    assert enqueued == 2
    assert captured_tasks["action"] == []
    assert len(captured_tasks["body_motion"]) == 2
