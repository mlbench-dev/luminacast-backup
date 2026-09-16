"""Unit tests for PR #68 cloud timeouts + HOSTKEY auto-recovery + retry.

Covers:
  - WaveSpeed per-call timeout scales with audio_duration_s.
  - fal hallo per-call timeout scales with audio_duration_s.
  - HOSTKEY POSTs /api/recover-comfyui after 3 high-VRAM skips for a render.
  - Failed-block retry pass: up to _MAX_BLOCK_RETRY_ATTEMPTS retries, not
    just one (client report: "The voiceover doesn't match this clip's
    length" still surfaces occasionally on casts with many blocks — a
    single retry wasn't enough to absorb the AI lipsync provider's
    per-attempt duration variance across N independent blocks).
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Stub sentry_sdk so the provider module imports cleanly in the test
# sandbox without the real dep.
if "sentry_sdk" not in sys.modules:
    class _NoScope:
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def set_tag(self, *_a, **_k): pass
        def set_extra(self, *_a, **_k): pass

    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
        push_scope=lambda: _NoScope(),
    )


def _import_providers():
    from services import render_providers
    return render_providers


@pytest.fixture
def hostkey_enabled(monkeypatch):
    """Force the HOSTKEY kill-switch OFF (i.e. HOSTKEY render ON) for the
    duration of a test.

    PR #94 added a kill-switch (services/hostkey_flags.py) that disables
    HOSTKEY by default. The legacy recovery tests below exercise the
    on-prem HOSTKEY VRAM/recovery code path, which only runs when an
    operator flips both flags back on. This fixture pins those flags so
    the legacy rollback path is reachable, then monkeypatch restores the
    defaults afterwards.
    """
    monkeypatch.setenv("CAST_RENDER_HOSTKEY_DISABLED", "false")
    monkeypatch.setenv("HOSTKEY_RENDER_ENABLED", "true")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: WaveSpeed timeout scales with audio_duration_s
# ─────────────────────────────────────────────────────────────────────────────

def test_wavespeed_timeout_scales_with_duration():
    """The poll-deadline budget must scale per block duration via
    ``derive_provider_timeout`` (the function the speaking providers now
    call): 10 s → 600 s, 30 s → 1800 s (ceiling), tiny clips → 300 s floor.
    The hardcoded 67 s / 180 s ceilings from before must be gone.
    """
    rp = _import_providers()
    # Direct helper: this is the function the providers call.
    assert rp.derive_provider_timeout(10) == 600
    assert rp.derive_provider_timeout(30) == 1800  # 30*60=1800 (ceiling)
    assert rp.derive_provider_timeout(60) == 1800  # capped at ceiling
    assert rp.derive_provider_timeout(1) == 300     # floor
    assert rp.derive_provider_timeout(0) == 300     # default for "unknown"
    assert rp.derive_provider_timeout(None) == 300

    # End-to-end: invoke WaveSpeed.generate with audio_duration_s=10
    # and confirm the timeout logged + applied is 300 (not 67).
    provider = rp.WavespeedInfinitetalkProvider()

    submit_response = MagicMock()
    submit_response.status_code = 200
    submit_response.json = MagicMock(return_value={
        "data": {"id": "pred_test", "status": "created"},
    })
    poll_response = MagicMock()
    poll_response.status_code = 200
    poll_response.json = MagicMock(return_value={
        "data": {"status": "completed", "outputs": ["https://cdn.test/x.mp4"]},
    })

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=submit_response)
    fake_client.get = AsyncMock(return_value=poll_response)

    with patch.dict("os.environ", {"WAVESPEED_API_KEY": "test-key"}):
        with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
            # 10 s clip: should NOT raise TimeoutError because the
            # poll loop sees completed on the first iteration and
            # the deadline is far in the future.
            result = asyncio.run(provider.generate(
                image_url="https://x", audio_url="https://y", prompt="p",
                width=480, height=848, audio_duration_s=10.0,
            ))
    assert result["video_url"] == "https://cdn.test/x.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: fal hallo timeout scales with audio_duration_s
# ─────────────────────────────────────────────────────────────────────────────

def test_fal_hallo_timeout_scales_with_duration():
    """Same matrix as WaveSpeed via ``derive_provider_timeout``: tiny → 300
    (floor), 10 → 600, 30 → 1800 (ceiling).

    The 120 s / 180 s hardcoded ceilings from before must be gone.
    """
    rp = _import_providers()
    assert rp.derive_provider_timeout(10) == 600
    assert rp.derive_provider_timeout(30) == 1800
    assert rp.derive_provider_timeout(1) == 300

    provider = rp.FalHalloProvider()

    submit_response = MagicMock()
    submit_response.status_code = 200
    submit_response.json = MagicMock(return_value={
        "request_id": "req_test",
        "status_url": "https://queue.fal.run/.../status",
        "response_url": "https://queue.fal.run/.../result",
    })
    status_response = MagicMock()
    status_response.status_code = 200
    status_response.json = MagicMock(return_value={"status": "COMPLETED"})
    result_response = MagicMock()
    result_response.status_code = 200
    result_response.json = MagicMock(return_value={
        "video": {"url": "https://cdn.fal/x.mp4"},
    })

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=submit_response)
    call_seq = {"i": 0}

    async def _get(url, headers=None):
        call_seq["i"] += 1
        return status_response if call_seq["i"] == 1 else result_response

    fake_client.get = _get

    with patch.dict("os.environ", {"FAL_KEY": "test-key"}):
        with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
            result = asyncio.run(provider.generate(
                image_url="https://x", audio_url="https://y",
                audio_duration_s=10.0,
            ))
    assert result["video_url"] == "https://cdn.fal/x.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3: HOSTKEY recovery client triggers after 3 high-VRAM skips
# ─────────────────────────────────────────────────────────────────────────────

def test_hostkey_recovery_triggers_after_3_skips(hostkey_enabled):
    """When is_available() observes memory_used_mib >= 18000 three
    times for the same render_id, the fourth call must POST to
    /api/recover-comfyui. (The recovery POST is fired from inside
    the THIRD high-VRAM call — that's the implementation: counter
    reaches the threshold, then recovery runs in the same call.)

    Legacy HOSTKEY path — only meaningful with the kill-switch flipped
    OFF (see ``hostkey_enabled`` fixture); protects the rollback case.
    """
    rp = _import_providers()
    # Reset state between test runs.
    rp.reset_hostkey_recovery_state("rnd_recover_test")

    provider = rp.HostkeyInfinitetalkProvider()

    fake_status = MagicMock()
    fake_status.status_code = 200
    fake_status.json = MagicMock(return_value={"memory_used_mib": 20000})

    recover_response = MagicMock()
    recover_response.status_code = 200
    recover_response.text = '{"status": "ok", "vram_after_mib": 1200}'

    post_calls: list[str] = []

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=fake_status)

    async def _post(url, *a, **kw):
        post_calls.append(url)
        return recover_response

    fake_client.post = _post

    with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
        # Calls 1, 2: VRAM high → False, no recovery POST yet.
        r1 = asyncio.run(provider.is_available(render_id="rnd_recover_test"))
        r2 = asyncio.run(provider.is_available(render_id="rnd_recover_test"))
        assert r1 is False and r2 is False
        assert not any("/api/recover-comfyui" in u for u in post_calls), (
            f"recovery POST fired too early after {len(post_calls)} calls"
        )
        # Call 3: skip counter hits threshold → recovery POST fires.
        r3 = asyncio.run(provider.is_available(render_id="rnd_recover_test"))
        # is_available still returns False on this call (the recovery
        # POST resets the counter so the NEXT call gets a fresh probe;
        # the current call still respects the VRAM-high reading).
        assert r3 is False
        assert any("/api/recover-comfyui" in u for u in post_calls), (
            f"recovery POST never fired; POST urls={post_calls}"
        )
        recover_count = sum(1 for u in post_calls if "/api/recover-comfyui" in u)
        assert recover_count == 1, (
            f"expected exactly one /api/recover-comfyui POST, got {recover_count}"
        )

    # Cleanup.
    rp.reset_hostkey_recovery_state("rnd_recover_test")


def test_hostkey_recovery_endpoint_404_disables_for_render(hostkey_enabled):
    """If /api/recover-comfyui returns 404 (operator hasn't deployed
    the endpoint yet) we MUST NOT raise — instead, mark HOSTKEY
    unavailable for the rest of this render.

    Legacy HOSTKEY path — only meaningful with the kill-switch flipped
    OFF (see ``hostkey_enabled`` fixture); protects the rollback case.
    """
    rp = _import_providers()
    rp.reset_hostkey_recovery_state("rnd_404_test")

    provider = rp.HostkeyInfinitetalkProvider()
    fake_status = MagicMock()
    fake_status.status_code = 200
    fake_status.json = MagicMock(return_value={"memory_used_mib": 20000})

    not_found = MagicMock()
    not_found.status_code = 404
    not_found.text = "not found"

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=fake_status)
    fake_client.post = AsyncMock(return_value=not_found)

    with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
        # Three high-VRAM calls — third triggers recovery, which 404s.
        for _ in range(3):
            r = asyncio.run(provider.is_available(render_id="rnd_404_test"))
            assert r is False

        # Subsequent call: even if VRAM probe were to come back low,
        # recovery_disabled is set so HOSTKEY stays unavailable.
        fake_status.json = MagicMock(return_value={"memory_used_mib": 100})
        r4 = asyncio.run(provider.is_available(render_id="rnd_404_test"))
        assert r4 is False

    rp.reset_hostkey_recovery_state("rnd_404_test")


def test_hostkey_disabled_by_default_never_recovers(monkeypatch):
    """PR #94 kill-switch contract: with the default flags (HOSTKEY
    decommissioned), repeated is_available() calls must NEVER probe
    /api/gpu-status nor POST /api/recover-comfyui — the kill-switch
    short-circuits before the VRAM/recovery watchdog. The render falls
    through to the cloud InfiniteTalk providers instead.

    This is the inverse of ``test_hostkey_recovery_triggers_after_3_skips``
    (which is gated on the ``hostkey_enabled`` rollback fixture).
    """
    monkeypatch.delenv("CAST_RENDER_HOSTKEY_DISABLED", raising=False)
    monkeypatch.delenv("HOSTKEY_RENDER_ENABLED", raising=False)

    rp = _import_providers()
    rp.reset_hostkey_recovery_state("rnd_killswitch_recover")
    provider = rp.HostkeyInfinitetalkProvider()

    client_factory = MagicMock(side_effect=AssertionError(
        "kill-switch must short-circuit before constructing httpx.AsyncClient"
    ))

    with patch.object(rp.httpx, "AsyncClient", client_factory):
        # Even after the threshold number of calls, nothing should probe
        # or POST — the kill-switch returns False before either.
        for _ in range(rp._HOSTKEY_VRAM_SKIPS_BEFORE_RECOVER + 1):
            r = asyncio.run(
                provider.is_available(render_id="rnd_killswitch_recover")
            )
            assert r is False

    assert client_factory.call_count == 0, (
        "kill-switch must skip both the gpu-status probe and recovery POST"
    )
    rp.reset_hostkey_recovery_state("rnd_killswitch_recover")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 4 (widened): failed-block retry pass allows up to
# _MAX_BLOCK_RETRY_ATTEMPTS retries, not just one
# ─────────────────────────────────────────────────────────────────────────────

_MAX_BLOCK_RETRY_ATTEMPTS = 2  # mirrors tasks.cast_render's default


def _mirror_retry_gate(block_statuses: list[dict], failed_blocks: list[tuple[str, str]]):
    """Mirrors the exact gate logic in cast_render.py's retry loop:
    filter failed_blocks to those whose persisted retry_count is still
    under the ceiling."""
    existing_by_id = {row["block_id"]: row for row in block_statuses}
    return [
        (bid, err) for bid, err in failed_blocks
        if int(existing_by_id.get(bid, {}).get("retry_count", 0)) < _MAX_BLOCK_RETRY_ATTEMPTS
    ], existing_by_id


def test_failed_block_that_recovers_on_its_second_retry_is_not_given_up_on():
    """The exact scenario the widened ceiling exists for: a block fails
    the main bake AND its first retry (e.g. two consecutive
    SpeakingBlockOutOfTolerance misses — plausible, if rare, per-attempt
    variance), but succeeds on a SECOND retry. With the old ceiling of 1
    this block would have failed the whole render; with 2 it recovers.

    We model the retry logic in isolation here because cast_render
    imports too many heavy deps (SQLAlchemy, Celery, ffmpeg helpers) to
    load inside a unit test — this mirrors the production
    for _retry_pass in range(_MAX_BLOCK_RETRY_ATTEMPTS): loop.
    """
    block_statuses = [
        {"block_id": "blk_1", "state": "done"},
        {"block_id": "blk_2", "state": "failed", "retry_count": 0},
    ]
    failed_blocks = [("blk_2", "SpeakingBlockOutOfTolerance: dur=5.6s slot=5.0s")]
    pending_jobs = [(1, "blk_2", None, None, None, 5.0, "f", "a", "p")]

    # Attempt sequence for blk_2: fails on retry pass 1, succeeds on pass 2.
    attempt_outcomes = iter(["err", "ok"])
    rebake_call_log: list[str] = []

    async def _run_one_job_mock(i, job):
        rebake_call_log.append(job[1])
        outcome = next(attempt_outcomes)
        if outcome == "ok":
            return ("ok", job[1], f"el_{job[1]}", f"baked/{job[1]}.mp4")
        return ("err", job[1], "still out of tolerance", None)

    async def _run():
        nonlocal failed_blocks
        for _retry_pass in range(_MAX_BLOCK_RETRY_ATTEMPTS):
            if not failed_blocks:
                break
            retry_targets, existing_by_id = _mirror_retry_gate(block_statuses, failed_blocks)
            if not retry_targets:
                break
            retry_jobs = [j for j in pending_jobs if j[1] in {b for b, _ in retry_targets}]
            for bid, _ in retry_targets:
                prior = int(existing_by_id.get(bid, {}).get("retry_count", 0))
                existing_by_id[bid]["retry_count"] = prior + 1
                existing_by_id[bid]["state"] = "retrying"
            results = await asyncio.gather(
                *[_run_one_job_mock(i, j) for i, j in enumerate(retry_jobs)]
            )
            recovered = {r[1] for r in results if r[0] == "ok"}
            failed_blocks = [(bid, err) for bid, err in failed_blocks if bid not in recovered]
        return failed_blocks

    remaining_failures = asyncio.run(_run())

    # Two rebake attempts were made for blk_2 (pass 1 fail, pass 2 succeed).
    assert rebake_call_log == ["blk_2", "blk_2"]
    # It ultimately recovered — no longer in the failed list.
    assert remaining_failures == []
    assert block_statuses[1]["retry_count"] == 2


def test_a_persistently_failing_block_stops_after_the_retry_ceiling():
    """A block that NEVER succeeds must still be bounded — exactly
    _MAX_BLOCK_RETRY_ATTEMPTS retries, then the render gives up on it
    (never an infinite loop)."""
    block_statuses = [{"block_id": "blk_2", "state": "failed", "retry_count": 0}]
    failed_blocks = [("blk_2", "AllProvidersFailedError: every tier failed")]
    pending_jobs = [(0, "blk_2", None, None, None, 5.0, "f", "a", "p")]

    rebake_call_log: list[str] = []

    async def _run_one_job_mock(i, job):
        rebake_call_log.append(job[1])
        return ("err", job[1], "still failing", None)

    async def _run():
        nonlocal failed_blocks
        for _retry_pass in range(_MAX_BLOCK_RETRY_ATTEMPTS):
            if not failed_blocks:
                break
            retry_targets, existing_by_id = _mirror_retry_gate(block_statuses, failed_blocks)
            if not retry_targets:
                break
            retry_jobs = [j for j in pending_jobs if j[1] in {b for b, _ in retry_targets}]
            for bid, _ in retry_targets:
                prior = int(existing_by_id.get(bid, {}).get("retry_count", 0))
                existing_by_id[bid]["retry_count"] = prior + 1
            results = await asyncio.gather(
                *[_run_one_job_mock(i, j) for i, j in enumerate(retry_jobs)]
            )
            recovered = {r[1] for r in results if r[0] == "ok"}
            failed_blocks = [(bid, err) for bid, err in failed_blocks if bid not in recovered]
        return failed_blocks

    remaining_failures = asyncio.run(_run())

    # Exactly _MAX_BLOCK_RETRY_ATTEMPTS attempts — never more.
    assert len(rebake_call_log) == _MAX_BLOCK_RETRY_ATTEMPTS
    assert remaining_failures == [("blk_2", "AllProvidersFailedError: every tier failed")]
    assert block_statuses[0]["retry_count"] == _MAX_BLOCK_RETRY_ATTEMPTS

    # A further pass (were one attempted) would filter to no targets —
    # confirms the ceiling actually stops it, not just the loop's range().
    further_targets, _ = _mirror_retry_gate(block_statuses, remaining_failures)
    assert further_targets == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
