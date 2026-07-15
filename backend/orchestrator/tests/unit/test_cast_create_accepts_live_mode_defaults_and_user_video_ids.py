"""PR #162 wiring — the cast create/update/patch request schemas accept the
LIVE-mode toggle payload (``live_mode_defaults`` + ``user_video_ids``).

DB-free by design (the CI "Backend Tests" unit job runs ``tests/unit/`` WITHOUT a
Postgres service): these assert the Pydantic contract the route relies on. The
route handlers (``create_cast`` / ``update_cast`` / ``patch_cast`` in
``routers.casts``) do nothing more than copy ``req.live_mode_defaults`` and
``req.user_video_ids`` onto the Cast row, so validating the schema round-trip is
what guarantees the fields are no longer silently dropped.
"""
import pytest

from schemas.cast import CastCreate, CastResponse
from routers.casts import CastPatchRequest


LIVE_DEFAULTS = {
    "voiceover": True,
    "broll": True,
    "max_duration_seconds": 90,
    "broll_cadence_seconds": 8,
}
VIDEO_IDS = ["uva_abc123", "uva_def456"]


def test_cast_create_accepts_live_mode_defaults_and_user_video_ids():
    req = CastCreate(
        avatar_id="avt_1",
        live_mode_defaults=LIVE_DEFAULTS,
        user_video_ids=VIDEO_IDS,
    )
    assert req.live_mode_defaults == LIVE_DEFAULTS
    assert req.user_video_ids == VIDEO_IDS


def test_cast_create_fields_default_to_none_for_recorded_casts():
    # Recorded casts omit the LIVE payload entirely — both must be optional and
    # default to None so existing clients keep working.
    req = CastCreate(avatar_id="avt_1")
    assert req.live_mode_defaults is None
    assert req.user_video_ids is None


def test_cast_patch_request_accepts_live_mode_payload():
    req = CastPatchRequest(
        live_mode_defaults=LIVE_DEFAULTS,
        user_video_ids=VIDEO_IDS,
    )
    assert req.live_mode_defaults == LIVE_DEFAULTS
    assert req.user_video_ids == VIDEO_IDS


def test_cast_response_round_trips_live_mode_payload():
    # The response model must surface both fields so the client can read back
    # what it posted (and so from_attributes maps the new Cast columns).
    from datetime import datetime

    resp = CastResponse(
        id="cst_1",
        status="DRAFT",
        avatar_id="avt_1",
        max_duration_minutes=240,
        loop=True,
        creation_paid=False,
        generation_progress=0.0,
        live_mode_defaults=LIVE_DEFAULTS,
        user_video_ids=VIDEO_IDS,
        created_at=datetime(2026, 6, 17),
    )
    assert resp.live_mode_defaults == LIVE_DEFAULTS
    assert resp.user_video_ids == VIDEO_IDS
