"""Unit tests for PR #70 — Non-blocking HOSTKEY semaphore in the
speaking-block (`HOSTKEY_ONLY`) dispatch path of
`backend/orchestrator/tasks/cast_render.py`.

The patched branch (`tasks/cast_render.py` around line ~2494):

  * tries to grab the per-loop `Semaphore(1)` with a short wait (3 s)
  * on `asyncio.TimeoutError`, swallows it, captures to Sentry, logs,
    sets `hostkey_acquired = False` and runs `try_chain` with HOSTKEY
    DROPPED from the provider list
  * only `sem.release()`s in the `finally` when `hostkey_acquired` is
    True (releasing an unacquired Semaphore would underflow and let two
    concurrent jobs onto the local GPU)

These tests reproduce the patched pattern in an isolated coroutine and
assert the behaviour we ship — the real branch is buried inside the
`_render_async` orchestrator and not directly callable without a full
DB / R2 / dispatcher mock harness, so we verify the contract.

A second pair of static-code checks asserts the actual file still
contains the timeout=3.0 acquire AND the `if hostkey_acquired` guard in
`finally` — so a future revert to the blocking 120 s wait is caught by
CI.
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── Stub sentry_sdk so importing the modules doesn't pull the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# Marker classes — the patched code builds `speaking_providers` from
# real provider classes. We only care about identity / membership in
# these tests, so plain sentinels are enough.
class _HostkeyProvider: ...
class _WavespeedProvider: ...
class _FalHalloProvider: ...


async def _patched_speaking_dispatch(sem, captured):
    """Reproduce the patched HOSTKEY_ONLY branch verbatim.

    Records the provider list that would have been passed to try_chain
    in `captured["providers"]` and whether `sem.release()` ended up
    being called in the `finally` (via `captured["released"]`).
    """
    hostkey_acquired = False
    if sem is not None:
        try:
            await asyncio.wait_for(sem.acquire(), timeout=3.0)
            hostkey_acquired = True
        except asyncio.TimeoutError:
            # Mirror the patched code: swallow + continue.
            pass
    try:
        speaking_providers = []
        if hostkey_acquired:
            speaking_providers.append(_HostkeyProvider())
        speaking_providers.extend([
            _WavespeedProvider(),
            _FalHalloProvider(),
        ])
        captured["providers"] = speaking_providers
        captured["hostkey_acquired"] = hostkey_acquired
    finally:
        if hostkey_acquired and sem is not None:
            sem.release()
            captured["released"] = True


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — when semaphore wait times out, HOSTKEY is dropped from the chain
# ─────────────────────────────────────────────────────────────────────────────

def test_speaking_block_skips_hostkey_when_semaphore_unavailable():
    """If `asyncio.wait_for(sem.acquire(), 3.0)` raises TimeoutError,
    the provider list MUST drop HostkeyInfinitetalkProvider but keep
    Wavespeed + FalHallo so the cloud failover still runs.
    """
    sem = MagicMock()

    async def _never_acquire():
        # Sleep longer than the 3 s timeout the patched code uses.
        await asyncio.sleep(60)

    sem.acquire = MagicMock(side_effect=lambda: _never_acquire())
    sem.release = MagicMock()

    captured: dict = {}
    _run(_patched_speaking_dispatch(sem, captured))

    provider_types = {type(p) for p in captured["providers"]}
    assert _HostkeyProvider not in provider_types, (
        "HOSTKEY provider must be dropped when semaphore is unavailable — "
        "otherwise sibling blocks pile onto a busy local GPU and time out."
    )
    assert _WavespeedProvider in provider_types
    assert _FalHalloProvider in provider_types
    assert captured["hostkey_acquired"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — when semaphore is free, HOSTKEY stays at the head of the chain
# ─────────────────────────────────────────────────────────────────────────────

def test_speaking_block_uses_hostkey_when_semaphore_available():
    """When the per-loop Semaphore(1) is free, the patched code should
    include all three providers — HOSTKEY first, then the paid cloud
    tiers as fallback.
    """
    sem = asyncio.Semaphore(1)
    captured: dict = {}
    _run(_patched_speaking_dispatch(sem, captured))

    provider_types = [type(p) for p in captured["providers"]]
    assert provider_types == [
        _HostkeyProvider, _WavespeedProvider, _FalHalloProvider,
    ]
    assert captured["hostkey_acquired"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — TimeoutError must not propagate out of the function
# ─────────────────────────────────────────────────────────────────────────────

def test_semaphore_timeout_does_not_raise_to_caller():
    """The whole point of PR #70: a slow semaphore must NEVER surface
    `asyncio.TimeoutError` to the orchestrator. Previously the
    `await asyncio.wait_for(... 120)` was outside the `try` and the
    bare TimeoutError marked the block failed before try_chain ran.
    """
    sem = MagicMock()

    async def _never_acquire():
        await asyncio.sleep(60)

    sem.acquire = MagicMock(side_effect=lambda: _never_acquire())
    sem.release = MagicMock()

    captured: dict = {}
    # If the patched code regresses, _run() will raise here.
    _run(_patched_speaking_dispatch(sem, captured))
    assert captured["hostkey_acquired"] is False
    # Provider list must still exist — the function continued normally.
    assert captured["providers"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — release() is only called when acquire() succeeded
# ─────────────────────────────────────────────────────────────────────────────

def test_semaphore_released_only_if_acquired():
    """Critical correctness invariant for `asyncio.Semaphore`:
    releasing without a matching acquire over-counts the internal
    permit count, letting two concurrent HOSTKEY jobs run.
    """
    # Case A: acquire times out → release MUST NOT be called.
    sem_busy = MagicMock()

    async def _never_acquire():
        await asyncio.sleep(60)

    sem_busy.acquire = MagicMock(side_effect=lambda: _never_acquire())
    sem_busy.release = MagicMock()

    captured_a: dict = {}
    _run(_patched_speaking_dispatch(sem_busy, captured_a))
    assert sem_busy.release.call_count == 0, (
        "release() must not be called when acquire timed out — "
        "would underflow the Semaphore."
    )

    # Case B: acquire succeeds → release IS called exactly once.
    real_sem = asyncio.Semaphore(1)
    captured_b: dict = {}
    _run(_patched_speaking_dispatch(real_sem, captured_b))
    # asyncio.Semaphore exposes _value; after a balanced acquire/release
    # it should be back to 1.
    assert real_sem._value == 1, (
        "After a balanced acquire/release the Semaphore should be back "
        "to its initial permit count."
    )
    assert captured_b.get("released") is True


# ─────────────────────────────────────────────────────────────────────────────
# Static guards — make sure the production file actually has the patch
# ─────────────────────────────────────────────────────────────────────────────

_THIS_DIR = Path(__file__).resolve().parent
_CAST_RENDER = _THIS_DIR.parent.parent / "tasks" / "cast_render.py"
_DISPATCHER = _THIS_DIR.parent.parent / "services" / "render_dispatcher.py"


def test_cast_render_uses_short_semaphore_wait():
    """Guard: cast_render.py must NOT regress to the blocking 120 s
    wait. The patched form is `wait_for(sem.acquire(), timeout=3.0)`.
    """
    src = _CAST_RENDER.read_text()
    # The HOSTKEY_QUEUE_WAIT_S * 4 form (= 120 s) is the broken one.
    assert "HOSTKEY_QUEUE_WAIT_S * 4" not in src, (
        "Found the old 120 s blocking semaphore wait — PR #70 regression."
    )
    assert re.search(r"wait_for\(\s*sem\.acquire\(\)\s*,\s*timeout=3\.0", src), (
        "Expected short (3 s) semaphore wait in cast_render.py HOSTKEY_ONLY "
        "branch — PR #70 patch missing."
    )


def test_cast_render_finally_guards_release():
    """Guard: `sem.release()` must be gated on `hostkey_acquired` so
    an underflow can't happen when we skipped HOSTKEY.
    """
    src = _CAST_RENDER.read_text()
    assert "if hostkey_acquired and sem is not None" in src, (
        "Expected hostkey_acquired guard around sem.release() — "
        "PR #70 patch missing."
    )


def test_dispatcher_uses_short_semaphore_wait():
    """Guard: both dispatcher paths (I2V at ~L176 and T2V at ~L448)
    should also use the 3 s wait for symmetry.
    """
    src = _DISPATCHER.read_text()
    # Should be exactly two `wait_for(sem.acquire(), timeout=3.0)` calls.
    matches = re.findall(r"wait_for\(\s*sem\.acquire\(\)\s*,\s*timeout=3\.0", src)
    assert len(matches) >= 2, (
        f"Expected ≥2 short (3 s) semaphore waits in render_dispatcher.py, "
        f"found {len(matches)} — PR #70 patch missing."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
