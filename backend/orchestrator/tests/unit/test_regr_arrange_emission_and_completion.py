"""regr: stock-track emission in auto_arrange + no premature-READY in webhooks.

Three bugs found while validating cast cst_89f282ba2af5:

  Bug 1 — pip_layout was read as a block attribute, but it actually lives in the
          JSONB ``metadata`` bag (``block_metadata`` attr). Covered by
          ``_get_pip_layout`` tests in test_regr_wiring_arrange_stock_overlays.

  Bug 2 — the saved timeline carried no ``stock`` overlay track, so the bonded
          avatar (face-only) composited the rest of the frame to black. This
          module asserts ``auto_arrange_cast_timeline`` appends a ``stock`` track
          (and stamps compositionWidth/Height = 480x848) for split_h + stock_video
          blocks that carry a stock_media_url.

  Bug 3 — ``_check_cast_completion`` moved the cast to READY while variants were
          still GENERATING/PENDING. This module asserts the cast stays out of
          READY until every variant reaches a terminal state.

Pure unit tests — fake ORM stand-ins, mocked db/r2/audit. No real DB or FFmpeg.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

CANVAS_W = 480
CANVAS_H = 848


# ── fakes ────────────────────────────────────────────────────────────────────

class _Variant:
    def __init__(self, *, vid="var1", status="READY", video_key="k.mp4",
                 audio_key="a.wav", tts_duration_seconds=3.0):
        self.id = vid
        self.status = status
        self.video_key = video_key
        self.audio_key = audio_key
        self.tts_duration_seconds = tts_duration_seconds
        self.caption_words = None
        self.sfx_timings = None
        self.final_video_key = None


class _Block:
    def __init__(self, *, bid, position, category="avatar_speaking",
                 pip_layout=None, stock_media_url=None, stock_media_kind=None,
                 variants=None):
        self.id = bid
        self.position = position
        self.category = category
        self.block_metadata = {"pip_layout": pip_layout} if pip_layout else {}
        self.stock_media_url = stock_media_url
        self.stock_media_kind = stock_media_kind
        self.is_active = True
        self.deleted_at = None
        self.variants = variants if variants is not None else [_Variant()]


class _Cast:
    def __init__(self, blocks):
        self.id = "cst_test"
        self.blocks = blocks
        self.timeline_json = None
        self.updated_at = None
        self.background_music_url = None
        self.music_track_choice = "auto"


# ── Bug 2: auto_arrange_cast_timeline emits a stock track ─────────────────────

def _run_arrange(cast):
    from routers import casts as casts_mod

    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = cast
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()

    r2 = MagicMock()
    r2.get_public_url.side_effect = lambda key, *a, **k: f"https://media/{key}"

    user = MagicMock()
    user.id = "usr_test"

    with patch("services.r2_storage.get_r2_storage_service", return_value=r2), \
         patch("services.audit_log.record", new=AsyncMock()), \
         patch("sqlalchemy.orm.attributes.flag_modified"):
        asyncio.run(
            casts_mod.auto_arrange_cast_timeline(cast.id, user=user, db=db)
        )

    return cast.timeline_json["default"]["twick_data"]


def test_arrange_emits_stock_track_for_split_h_and_stock_video():
    blocks = [
        _Block(bid="b0", position=0),  # plain avatar, no stock
        _Block(bid="b1", position=1, category="pip_talking_head",
               pip_layout="split_h", stock_media_url="https://x/broll.mp4",
               stock_media_kind="video"),
        _Block(bid="b2", position=2, category="stock_video",
               stock_media_url="https://x/clip.mp4", stock_media_kind="video"),
        _Block(bid="b3", position=3, pip_layout="fullscreen"),  # no stock
    ]
    twick = _run_arrange(_Cast(blocks))

    # Canvas stamped 9:16 portrait so the renderer scales overlays 1:1.
    assert twick["compositionWidth"] == CANVAS_W
    assert twick["compositionHeight"] == CANVAS_H

    track_ids = [t["id"] for t in twick["tracks"]]
    assert "stock" in track_ids, "stock overlay track must be appended"

    stock = next(t for t in twick["tracks"] if t["id"] == "stock")
    assert stock["type"] == "video"
    # Exactly the two stock-bearing blocks emit elements.
    emitted_blocks = {e["metadata"]["block_id"] for e in stock["elements"]}
    assert emitted_blocks == {"b1", "b2"}

    for el in stock["elements"]:
        assert el["props"]["src"].startswith("https://x/")
        # Non-bonded so extract_overlay_elements surfaces it at render time.
        assert el["metadata"].get("bonded") is not True


def test_arrange_emits_no_stock_track_when_no_stock_blocks():
    blocks = [_Block(bid="b0", position=0), _Block(bid="b1", position=1)]
    twick = _run_arrange(_Cast(blocks))
    track_ids = [t["id"] for t in twick["tracks"]]
    assert "stock" not in track_ids


# ── Bug 3: _check_cast_completion never marks READY while in-flight ──────────

class _WebhookVariant:
    def __init__(self, status):
        self.status = status
        self.video_key = "k.mp4"
        self.final_video_key = None


def _run_check_completion(cast, variants):
    from routers import webhooks as wh

    db = AsyncMock()
    scalars = MagicMock()
    scalars.all.return_value = variants
    exec_result = MagicMock()
    exec_result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()

    asyncio.run(wh._check_cast_completion(db, cast))


def test_completion_does_not_mark_ready_while_variant_generating():
    from models.variant import VariantStatus
    from models.cast import CastStatus

    cast = MagicMock()
    cast.id = "cst_test"
    cast.status = CastStatus.GENERATING
    cast.timeline_json = None

    variants = [
        _WebhookVariant(VariantStatus.READY),
        _WebhookVariant(VariantStatus.GENERATING),  # still in-flight
    ]
    _run_check_completion(cast, variants)

    assert cast.status != CastStatus.READY
    # Progress moved, but the cast stays in its non-terminal state.
    assert cast.status == CastStatus.GENERATING


def test_completion_marks_ready_only_when_all_terminal():
    from models.variant import VariantStatus
    from models.cast import CastStatus

    cast = MagicMock()
    cast.id = "cst_test"
    cast.status = CastStatus.GENERATING
    cast.timeline_json = None

    variants = [
        _WebhookVariant(VariantStatus.READY),
        _WebhookVariant(VariantStatus.READY),
    ]
    _run_check_completion(cast, variants)

    assert cast.status == CastStatus.READY


def test_completion_partial_failure_is_ready_when_no_pending():
    from models.variant import VariantStatus
    from models.cast import CastStatus

    cast = MagicMock()
    cast.id = "cst_test"
    cast.status = CastStatus.GENERATING
    cast.timeline_json = None

    variants = [
        _WebhookVariant(VariantStatus.READY),
        _WebhookVariant(VariantStatus.FAILED),
    ]
    _run_check_completion(cast, variants)

    assert cast.status == CastStatus.READY
