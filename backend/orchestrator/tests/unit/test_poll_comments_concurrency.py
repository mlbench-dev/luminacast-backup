"""Unit tests for PR #79 — Fix asyncpg concurrent-operation crash in
poll_comments_task.

Background: a shared AsyncSession was being reused across multiple
post-refresh awaits in `_poll_comments_async`. asyncpg connections cannot
serve concurrent operations — when one was already in flight, the next
operation raised
`InterfaceError: cannot perform operation: another operation is in progress`.

The fix gives each post its own session scope (its own asyncpg connection
checkout) so concurrent or overlapping refreshes never share a connection.
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

import pytest


# Stub sentry_sdk so importing the modules under test doesn't require the
# real dependency.
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )


ORCH_ROOT = Path(__file__).resolve().parents[2]
SOCIAL_TASKS_PATH = ORCH_ROOT / "tasks" / "social_tasks.py"
DATABASE_PATH = ORCH_ROOT / "database.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Static contract: code shape ──────────────────────────────────────────


def test_database_engine_has_pool_pre_ping_and_recycle() -> None:
    src = _read(DATABASE_PATH)
    assert "pool_pre_ping=True" in src, (
        "database engine must enable pool_pre_ping so stale asyncpg "
        "connections are detected before reuse"
    )
    assert re.search(r"pool_recycle\s*=\s*\d+", src), (
        "database engine must set pool_recycle to bound connection age"
    )


def test_poll_comments_uses_per_post_session_scope() -> None:
    """The refresh loop must open a fresh session per post — sharing one
    asyncpg connection across awaits is exactly what produced the
    InterfaceError this PR fixes."""
    src = _read(SOCIAL_TASKS_PATH)
    # The body of the refresh loop must contain `async with
    # async_session_factory()` — i.e. each iteration opens its own session
    # rather than relying on a single outer `db`.
    refresh_loop = src[src.index("for cand in candidates"):]
    end = refresh_loop.find("\n    logger.info")
    refresh_loop = refresh_loop[: end if end != -1 else len(refresh_loop)]
    # Round-6: the poller now builds a task-local session factory
    # (``session_factory``) bound to the current event loop, but the per-post
    # scope contract is unchanged — each iteration opens its own session.
    assert "async with session_factory()" in refresh_loop, (
        "per-post session scope is required to avoid sharing an asyncpg "
        "connection across concurrent awaits"
    )


def test_poll_comments_excepts_capture_sentry() -> None:
    src = _read(SOCIAL_TASKS_PATH)
    excepts = [m.start() for m in re.finditer(r"except\s+Exception", src)]
    assert excepts, "module should have at least one except clause"
    for pos in excepts:
        block = src[pos : pos + 400]
        assert "sentry_sdk.capture_exception" in block, (
            "every except in social_tasks.py must call "
            "sentry_sdk.capture_exception before continuing"
        )


# ── Behavioural: concurrent refresh on independent sessions ──────────────


class _FakeAsyncpgConn:
    """Mimics asyncpg's single-operation-at-a-time constraint.

    A second concurrent `execute` raises InterfaceError, exactly like the
    real driver. Each instance models one underlying connection.
    """

    def __init__(self) -> None:
        self._busy = False

    async def execute(self, _label: str) -> str:
        if self._busy:
            raise RuntimeError(
                "InterfaceError: cannot perform operation: "
                "another operation is in progress"
            )
        self._busy = True
        try:
            await asyncio.sleep(0.01)
            return "ok"
        finally:
            self._busy = False


class _FakeSession:
    """A session bound to its own asyncpg connection."""

    def __init__(self) -> None:
        self.conn = _FakeAsyncpgConn()

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_a) -> None:
        return None

    async def execute(self, label: str) -> str:
        return await self.conn.execute(label)


class _SharedSessionFactory:
    """Every call returns the same session → same underlying connection.
    This models the old buggy code where one outer `db` was reused."""

    def __init__(self) -> None:
        self._session = _FakeSession()

    def __call__(self) -> _FakeSession:
        return self._session


class _PerPostSessionFactory:
    """Each call returns a brand new session → distinct connection.
    This models the new fix."""

    def __call__(self) -> _FakeSession:
        return _FakeSession()


async def _refresh_one(session_factory, post_id: str) -> str:
    async with session_factory() as db:
        return await db.execute(f"refresh:{post_id}")


def test_shared_session_factory_reproduces_concurrent_op_error() -> None:
    """Sanity check: with a shared session, simulating two overlapping
    refresh calls reproduces the asyncpg error this PR is fixing."""

    async def _run() -> None:
        factory = _SharedSessionFactory()
        await asyncio.gather(
            _refresh_one(factory, "post_a"),
            _refresh_one(factory, "post_b"),
        )

    with pytest.raises(RuntimeError, match="another operation is in progress"):
        asyncio.run(_run())


def test_per_post_session_factory_handles_concurrent_refresh() -> None:
    """The fix: each refresh opens its own session, so concurrent refresh
    calls succeed without the InterfaceError."""

    async def _run() -> list[str]:
        factory = _PerPostSessionFactory()
        return await asyncio.gather(
            _refresh_one(factory, "post_a"),
            _refresh_one(factory, "post_b"),
            _refresh_one(factory, "post_c"),
        )

    results = asyncio.run(_run())
    assert results == ["ok", "ok", "ok"]
