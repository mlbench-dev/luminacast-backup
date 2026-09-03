"""Cast PATCH: `quality` is render-only, script-shaping fields need `regen`.

DB-free schema-contract coverage (the CI unit job has no Postgres). The gate
logic itself lives in ``patch_cast`` and is exercised by the integration
suite; these pin the request contract the frontend relies on:

  * ``quality`` can be sent on its own (no `regen`) — it never rebuilds the
    script, so SetupPhase sends it on every Continue.
  * ``regen`` is an explicit optional bool that, when true, tells the handler
    to accept avatar / products / template / cast_type / production_level on a
    cast that's already past script/audio (and mark the audio stale).
"""
from routers.casts.crud import CastPatchRequest


def test_quality_accepted_without_regen():
    req = CastPatchRequest(quality="hd_plus")
    assert req.quality == "hd_plus"
    assert req.regen is None  # not implied


def test_regen_defaults_to_none():
    assert CastPatchRequest().regen is None


def test_regen_flag_round_trips_with_script_shaping_fields():
    req = CastPatchRequest(
        regen=True,
        avatar_id="avt_9",
        cast_type="recorded",
        production_level="premium",
        template_id="tmpl_x",
        product_ids=["prod_1", "prod_2"],
    )
    assert req.regen is True
    assert req.avatar_id == "avt_9"
    assert req.production_level == "premium"
    assert req.product_ids == ["prod_1", "prod_2"]


def test_quality_and_regen_are_independent():
    # A pure quality bump — no regen, no script-shaping fields.
    q_only = CastPatchRequest(quality="simple")
    assert q_only.quality == "simple" and q_only.regen is None
    # A regen with no quality change.
    r_only = CastPatchRequest(regen=True, production_level="quick")
    assert r_only.quality is None and r_only.regen is True
