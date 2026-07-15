"""Round-6 Bug B — outline framing normalisation.

The user wanted camera-framing variety across avatar blocks instead of the
same medium-close shot every time. The LLM now emits a ``framing`` enum per
scene; ``_sanitize_outline_framing`` coerces any missing / invalid value to a
valid ``ShotFraming`` (default MEDIUM) so a malformed response can't break
look generation.

These tests pin the sanitiser and the FLUX prompt-fragment mapping.
"""
from engine import cast_generator as cg
from models.avatar_look import (
    DEFAULT_FRAMING,
    FRAMING_PROMPTS,
    ShotFraming,
    framing_prompt_fragment,
)


def test_valid_framing_is_preserved():
    scene = {"framing": "CLOSE"}
    cg._sanitize_outline_framing(scene)
    assert scene["framing"] == "CLOSE"


def test_lowercase_framing_is_normalised_to_upper():
    scene = {"framing": "angle_left_3q"}
    cg._sanitize_outline_framing(scene)
    assert scene["framing"] == "ANGLE_LEFT_3Q"


def test_missing_framing_defaults_to_medium():
    scene = {}
    cg._sanitize_outline_framing(scene)
    assert scene["framing"] == "MEDIUM"


def test_invalid_framing_falls_back_to_medium():
    scene = {"framing": "extreme-dutch-tilt"}
    cg._sanitize_outline_framing(scene)
    assert scene["framing"] == "MEDIUM"


def test_sanitize_categories_assigns_framing_to_every_scene():
    scenes = [
        {"category": "avatar_speaking", "framing": "CLOSE"},
        {"category": "avatar_speaking"},  # missing -> MEDIUM
        {"category": "product_demo", "framing": "bogus"},  # invalid -> MEDIUM
    ]
    cleaned = cg._sanitize_outline_categories(scenes, cast_id="cst_test")
    for s in cleaned:
        assert s["framing"] in cg._VALID_FRAMINGS
    assert cleaned[0]["framing"] == "CLOSE"
    assert cleaned[1]["framing"] == "MEDIUM"
    assert cleaned[2]["framing"] == "MEDIUM"


def test_every_framing_enum_has_a_prompt_fragment():
    for f in ShotFraming:
        assert f.value in FRAMING_PROMPTS
        assert FRAMING_PROMPTS[f.value].strip()


def test_framing_prompt_fragment_defaults_to_medium():
    assert framing_prompt_fragment(None) == FRAMING_PROMPTS[DEFAULT_FRAMING]
    assert framing_prompt_fragment("nonsense") == FRAMING_PROMPTS[DEFAULT_FRAMING]
    assert framing_prompt_fragment("close") == FRAMING_PROMPTS["CLOSE"]
