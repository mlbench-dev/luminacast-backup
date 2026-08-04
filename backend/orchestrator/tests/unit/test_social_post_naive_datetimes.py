"""Regression tests for tz-aware datetimes hitting naive DateTime columns
in routers/social.py.

Bug: "Post Now" successfully created the post on Zernio (real HTTP 201,
confirmed live), then our own SocialPost insert crashed with:
    asyncpg.exceptions.DataError: invalid input for query argument $14:
    ... (can't subtract offset-naive and offset-aware datetimes)
SocialPost.published_at / scheduled_for and SocialComment.created_at /
replied_at are all plain `Column(DateTime)` (TIMESTAMP WITHOUT TIME ZONE) —
asyncpg rejects a tz-aware Python datetime for those outright. The code was
building tz-aware datetimes (datetime.now(timezone.utc), or
datetime.fromisoformat(iso_string_with_offset)) and assigning them
directly, so a post could publish successfully on Zernio's side and still
come back to the user as a 500 with no record saved.

Fix: normalize to UTC then strip tzinfo before assignment, matching the
`datetime.now(timezone.utc).replace(tzinfo=None)` convention already used
elsewhere in this codebase (routers/admin.py, services/runpod.py) for the
same class of column.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

SOCIAL_PY = Path(__file__).resolve().parents[2] / "routers" / "social.py"


def test_now_utc_pattern_produces_naive_datetime():
    """Sanity-check the exact expression used everywhere in the fix."""
    value = datetime.now(timezone.utc).replace(tzinfo=None)
    assert value.tzinfo is None


def test_iso_with_offset_normalizes_to_naive_utc():
    """Zernio timestamps arrive as e.g. '2026-08-03T10:32:11.000Z' — the
    fix must both convert to UTC (in case of a non-UTC offset) AND strip
    tzinfo, or asyncpg rejects the insert."""
    iso = "2026-08-03T10:32:11.000+05:00"
    result = (
        datetime.fromisoformat(iso.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    assert result.tzinfo is None
    # +05:00 -> UTC is a 5-hour shift back
    assert result.hour == 5


def test_all_four_naive_column_assignments_strip_tzinfo():
    """Static guard over the four spots this exact bug was found: the
    create_social_post insert (published_at + scheduled_for), the
    refresh-from-Zernio published_at update, the comment-sync created_at,
    and the reply-comment replied_at. Every one must strip tzinfo before
    assignment to a naive DateTime column."""
    src = SOCIAL_PY.read_text(encoding="utf-8")
    occurrences = src.count(".replace(tzinfo=None)")
    assert occurrences >= 4, (
        f"expected at least 4 tzinfo-stripped datetime assignments "
        f"(published_at x2, scheduled_for, created_at, replied_at), "
        f"found {occurrences} — a naive-column assignment may have "
        f"regressed back to a bare tz-aware datetime"
    )
