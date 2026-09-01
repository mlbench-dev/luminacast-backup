"""PR-F — Stage-1 creative template catalog + outline constraint wiring.

Pure unit tests (no DB, no network):
  * the 6 launch templates load and expose the required fields
  * block sequences only reference valid BlockType values
  * caption presets exist in the frontend captionPresets.ts lib (no drift)
  * get_template lookup + Auto (null) fallthrough
  * _build_template_constraint renders nothing for Auto and a real
    constraint (sequence + bias) for a picked template
"""
import os
import re

import pytest

from models.block import BlockType
from services.cast_templates import TEMPLATES, list_templates, get_template
from engine.cast_generator import _build_template_constraint

EXPECTED_IDS = {
    "talking_head_hook",
    "demo_heavy",
    "multi_angle_story",
    "social_proof_stack",
    "before_after_reveal",
    "mic_on_creator_vlog",
}

_VALID_BLOCK_TYPES = {bt.value for bt in BlockType}

_CAPTION_PRESETS_TS = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "..",
        "frontend", "companion-app", "src", "lib", "captionPresets.ts",
    )
)


def test_six_templates_with_expected_ids():
    assert set(TEMPLATES.keys()) == EXPECTED_IDS
    assert len(TEMPLATES) == 6


@pytest.mark.parametrize("tpl_id", sorted(EXPECTED_IDS))
def test_each_template_has_required_fields(tpl_id):
    tpl = TEMPLATES[tpl_id]
    assert tpl["id"] == tpl_id
    assert isinstance(tpl["name"], str) and tpl["name"]
    assert isinstance(tpl["description"], str) and tpl["description"]
    assert isinstance(tpl["block_sequence"], list) and tpl["block_sequence"]
    assert isinstance(tpl["default_mic_on"], bool)
    assert isinstance(tpl["default_caption_preset"], str) and tpl["default_caption_preset"]
    assert len(tpl["est_duration_range"]) == 2
    assert isinstance(tpl.get("video_generation_prompt"), str) and tpl["video_generation_prompt"]
    assert isinstance(tpl.get("visual_rules"), list) and len(tpl["visual_rules"]) > 0
    assert isinstance(tpl.get("script_direction"), str) and tpl["script_direction"]
    bias = tpl["bias"]
    assert set(bias.keys()) == {"avatar_speaking", "broll", "uploaded_video"}
    # Bias is a rough split — allow a little slack but it should sum near 1.
    assert 0.9 <= sum(bias.values()) <= 1.1


@pytest.mark.parametrize("tpl_id", sorted(EXPECTED_IDS))
def test_block_sequences_use_valid_block_types(tpl_id):
    for bt in TEMPLATES[tpl_id]["block_sequence"]:
        assert bt in _VALID_BLOCK_TYPES, f"{tpl_id}: unknown block type {bt!r}"


def test_list_templates_shape():
    items = list_templates()
    assert len(items) == 6
    for item in items:
        assert set(item.keys()) == {
            "id", "name", "description", "preview_image_key", "block_count",
            "est_duration_range", "default_bias", "default_mic_on",
            "default_caption_preset", "video_generation_prompt", "visual_rules",
            "script_direction",
        }
        assert item["block_count"] == len(TEMPLATES[item["id"]]["block_sequence"])
        assert item["video_generation_prompt"] is not None
        assert isinstance(item["visual_rules"], list)
        assert item["script_direction"] is not None


def test_get_template_lookup_and_auto_fallthrough():
    assert get_template("talking_head_hook")["name"] == "Talking Head Hook"
    # Auto / unknown ids return None so the caller falls back to free choice.
    assert get_template(None) is None
    assert get_template("") is None
    assert get_template("does_not_exist") is None


def test_caption_presets_exist_in_frontend_lib():
    if not os.path.exists(_CAPTION_PRESETS_TS):
        pytest.skip("frontend captionPresets.ts not present in this checkout")
    with open(_CAPTION_PRESETS_TS, encoding="utf-8") as f:
        source = f.read()
    defined = set(re.findall(r'id:\s*"([a-z0-9_]+)"', source))
    used = {t["default_caption_preset"] for t in TEMPLATES.values()}
    missing = used - defined
    assert not missing, f"caption presets not defined in lib: {sorted(missing)}"


def test_constraint_empty_for_auto():
    # Auto mode (no template) must not change the prompt at all.
    assert _build_template_constraint(None) == ""


def test_constraint_renders_sequence_and_bias():
    constraint = _build_template_constraint(get_template("demo_heavy"))
    assert "Demo Heavy" in constraint
    # Sequence beats appear, joined by the arrow.
    assert "HOOK -> PRODUCT_DEMO" in constraint
    # Bias percentages render (demo_heavy is 30/70/0).
    assert "30% avatar speaking" in constraint
    assert "70% b-roll" in constraint
    assert "DEMO HEAVY FORMAT" in constraint
    assert "Visual Composition Rules" in constraint
    assert "Script & Pacing Rules" in constraint


@pytest.mark.parametrize("tpl_id", sorted(EXPECTED_IDS))
def test_all_templates_render_custom_prompt_directives(tpl_id):
    tpl = get_template(tpl_id)
    constraint = _build_template_constraint(tpl)
    assert tpl["name"] in constraint
    assert tpl["video_generation_prompt"] in constraint
    assert tpl["script_direction"] in constraint
