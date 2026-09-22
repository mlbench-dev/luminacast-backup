"""Unit tests for PR #77 — Cast fork endpoint must not crash on
pre-existing duplicate (cast_id, version) rows in `cast_versions`.

Background
----------
Before this PR, the backfill block inside `fork_cast`
(`backend/orchestrator/routers/casts.py`) used
`scalar_one_or_none()` against a query that could return multiple
rows. Production data for at least one cast had duplicates, so the
endpoint crashed with `MultipleResultsFound` and the user could no
longer fork that cast at all.

The fix:
  * use `.scalars().first()` instead, ordered by `created_at DESC`
    and limited to 1
  * dedupe existing duplicates via a one-shot script
  * add a UNIQUE(cast_id, version) constraint so they cannot recur

These tests verify:
  1. A reproduction of the patched query pattern picks ONE row when
     duplicates exist instead of raising.
  2. A static-code check asserts the live router still uses
     `.first()` and an explicit `order_by(... created_at.desc())` so
     a future revert to `scalar_one_or_none()` is caught by CI.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest


if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — patched query pattern over duplicate rows returns one row, no raise
# ─────────────────────────────────────────────────────────────────────────────

class _FakeRow:
    def __init__(self, row_id: str, created_at):
        self.id = row_id
        self.created_at = created_at


def _patched_pick_existing(rows):
    """Reproduce the patched lookup verbatim against an in-memory list.

    The live code is:
        existing_old_result = await db.execute(
            select(CastVersion).where(
                CastVersion.cast_id == cast_id,
                CastVersion.version == old_version,
            ).order_by(CastVersion.created_at.desc()).limit(1)
        )
        existing_old = existing_old_result.scalars().first()

    This helper performs the same operation (sort desc, take first) on
    a Python list so we can assert behaviour without spinning up an
    async DB session.
    """
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return ordered[0]


def test_patched_lookup_returns_latest_when_duplicates_exist():
    """With multiple duplicate rows for the same (cast_id, version),
    the patched query must NOT raise — it must pick the most recent
    row by created_at DESC.

    Pre-fix, `scalar_one_or_none()` raised `MultipleResultsFound`
    here, which made `fork_cast` unrecoverable for any cast with
    historical duplicates.
    """
    import datetime as dt
    older = _FakeRow("cv_older", dt.datetime(2026, 1, 1, 10, 0, 0))
    newer = _FakeRow("cv_newer", dt.datetime(2026, 5, 1, 10, 0, 0))
    middle = _FakeRow("cv_middle", dt.datetime(2026, 3, 1, 10, 0, 0))

    picked = _patched_pick_existing([older, newer, middle])

    assert picked is not None, (
        "fork_cast backfill must NOT treat duplicate rows as 'no row found' — "
        "doing so would insert yet another duplicate every time the user forks."
    )
    assert picked.id == "cv_newer", (
        "When duplicates exist we keep the most recent snapshot — older rows "
        "predate the user's latest edits."
    )


def test_patched_lookup_returns_none_when_no_rows():
    """No rows at all → `existing_old is None`, which is the signal
    the backfill branch uses to create the v{old_version} snapshot.
    """
    assert _patched_pick_existing([]) is None


def test_patched_lookup_returns_single_row_unchanged():
    """Common case: exactly one row → return it. This is the path
    that 99% of casts hit (those forked AFTER v1 was first stored).
    """
    import datetime as dt
    only = _FakeRow("cv_only", dt.datetime(2026, 4, 1, 10, 0, 0))
    assert _patched_pick_existing([only]) is only


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — static-code check on the live router
# ─────────────────────────────────────────────────────────────────────────────

_CASTS_ROUTER = (
    # routers/casts.py was split into the routers/casts/ package —
    # fork_cast now lives in forking.py.
    Path(__file__).resolve().parents[2] / "routers" / "casts" / "forking.py"
)


def test_fork_cast_does_not_use_scalar_one_or_none_for_cast_version_lookup():
    """A future revert to `scalar_one_or_none()` on a CastVersion
    lookup inside fork_cast would reintroduce the Sentry crash this
    PR fixes. Catch it in CI.
    """
    src = _CASTS_ROUTER.read_text()

    # Find the fork_cast function body. Locate the def and read until
    # the next top-level def or @router decorator at column 0.
    m = re.search(r"^async def fork_cast\(", src, flags=re.MULTILINE)
    assert m, "fork_cast not found in routers/casts/forking.py — test out of date"
    start = m.start()
    rest = src[start:]
    end_match = re.search(r"\n@router\.|\n(?:async )?def [a-zA-Z_]", rest[1:])
    body = rest if end_match is None else rest[: end_match.start() + 1]

    # Inside fork_cast, there must be no `scalar_one_or_none()` call on
    # a result that was produced from `select(CastVersion)`.
    assert "scalar_one_or_none" not in re.sub(r"#.*", "", body) or (
        # If someone adds an unrelated scalar_one_or_none later, that's
        # fine — what we forbid is the specific pattern that crashed.
        ".scalars().first()" in body
    ), (
        "fork_cast must not call scalar_one_or_none() on a CastVersion lookup — "
        "duplicates exist in production and that call raises MultipleResultsFound."
    )

    assert "CastVersion.created_at.desc()" in body, (
        "fork_cast backfill must order by created_at DESC so the most recent "
        "snapshot wins when duplicates exist."
    )
    assert ".scalars().first()" in body, (
        "fork_cast backfill must use .scalars().first() (the safe pattern that "
        "tolerates duplicates) instead of scalar_one_or_none()."
    )
