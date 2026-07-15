"""Step 1 — layout-template preset seeder (schema validation, no DB).

Validates that every one of the 10 preset configs conforms to the documented
CONFIG_SCHEMA, that there are exactly 10 with unique ids, and that every
caption_preset id the seeder references actually exists in the frontend
captionPresets.ts (guards the substitutions against future drift).

DB-backed idempotency + API tests live in
tests/integration/test_layout_templates_api.py (they need Postgres).
"""
import os
import re

import pytest
from jsonschema import validate as jsonschema_validate

from scripts.seed_layout_templates import CONFIG_SCHEMA, PRESETS, _preset_id

_USED_CAPTION_PRESETS = {p[2]["caption_preset"] for p in PRESETS}

_CAPTION_PRESETS_TS = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "..",
        "frontend", "companion-app", "src", "lib", "captionPresets.ts",
    )
)


def test_exactly_ten_presets_with_unique_ids():
    assert len(PRESETS) == 10
    ids = [_preset_id(slug) for slug, _, _ in PRESETS]
    assert len(set(ids)) == 10


@pytest.mark.parametrize("slug,name,config", PRESETS, ids=[p[0] for p in PRESETS])
def test_each_config_validates_against_schema(slug, name, config):
    # Raises jsonschema.ValidationError on failure.
    jsonschema_validate(instance=config, schema=CONFIG_SCHEMA)
    # Step-1 overlay defaults are uniform across all 10.
    assert config["overlay"] == {
        "anchor": "bottom_center",
        "width_frac": 0.28,
        "margin": 40,
    }


def test_caption_presets_exist_in_frontend_lib():
    """Every caption_preset id the seeder uses must exist in captionPresets.ts."""
    if not os.path.exists(_CAPTION_PRESETS_TS):
        pytest.skip("frontend captionPresets.ts not present in this checkout")
    with open(_CAPTION_PRESETS_TS, encoding="utf-8") as f:
        source = f.read()
    defined = set(re.findall(r'id:\s*"([a-z0-9_]+)"', source))
    missing = _USED_CAPTION_PRESETS - defined
    assert not missing, f"caption presets not defined in lib: {sorted(missing)}"
