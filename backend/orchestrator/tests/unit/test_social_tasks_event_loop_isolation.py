"""The Zernio comment poller must not reuse the shared module-level engine.

Celery runs ``_poll_comments_async`` via ``asyncio.run()``, which creates a
fresh event loop per task run. The shared ``database.async_session_factory``
binds its asyncpg pool to whatever loop first touched it, so reusing it across
runs raises "Task got Future attached to a different loop". The poller now
builds a task-local ``NullPool`` engine inside the run and disposes it in a
``finally`` so no connection outlives the loop.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

ORCH_ROOT = Path(__file__).resolve().parents[2]
SOCIAL_TASKS_PATH = ORCH_ROOT / "tasks" / "social_tasks.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_poller_does_not_use_shared_session_factory():
    src = _read(SOCIAL_TASKS_PATH)
    # The shared factory binds its pool to a foreign loop — must not be used.
    assert "from database import async_session_factory" not in src
    assert "_make_task_session_factory" in src


def test_poller_uses_nullpool_and_disposes_engine():
    src = _read(SOCIAL_TASKS_PATH)
    assert "NullPool" in src
    assert "poolclass=NullPool" in src
    assert "await engine.dispose()" in src


def test_task_session_factory_builds_nullpool_engine():
    from tasks.social_tasks import _make_task_session_factory
    from sqlalchemy.pool import NullPool

    engine, factory = _make_task_session_factory()
    try:
        assert isinstance(engine.pool, NullPool)
        assert factory is not None
    finally:
        # Dispose synchronously via a throwaway loop so the test leaves no
        # connection behind (mirrors the task's own finally-dispose).
        asyncio.run(engine.dispose())


def test_dispose_is_sentry_guarded():
    # Even engine.dispose() is wrapped so a teardown failure can't mask the
    # task result. RULES.md: every except calls capture_exception.
    src = _read(SOCIAL_TASKS_PATH)
    finally_block = src.split("finally:", 1)[1]
    assert "sentry_sdk.capture_exception(e)" in finally_block
