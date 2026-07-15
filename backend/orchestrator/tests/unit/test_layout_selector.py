"""Step 2 — content_type -> layout-template selection (pure function).

Covers ``services.layout_selector.select_layout_template`` for every content
type id declared in ``services/content_type.py``, plus the unknown-id and
``None`` fallbacks.

DB-free by design: the CI "Backend Tests" job runs ``tests/unit/`` WITHOUT a
Postgres service, so this test factories the Step-1 preset rows directly from
``scripts.seed_layout_templates.PRESETS`` into ``LayoutTemplate`` ORM objects and
feeds them through a tiny fake async session that mimics the
``execute(...).scalars().all()/.first()`` contract the selector relies on. It
does NOT depend on production DB state.
"""
import asyncio

import pytest

from models.layout_template import LayoutTemplate
from scripts.seed_layout_templates import PRESETS, _preset_id
from services.content_type import CONTENT_TYPES
from services.layout_selector import FALLBACK_TEMPLATE_ID, select_layout_template


def _build_preset_rows() -> list[LayoutTemplate]:
    """Factory the 10 Step-1 presets as detached ORM rows (no DB)."""
    return [
        LayoutTemplate(id=_preset_id(slug), user_id=None, name=name,
                       config=config, is_preset=True)
        for slug, name, config in PRESETS
    ]


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Minimal async session: filters the factoried rows the way the real
    SQLAlchemy SELECTs in layout_selector would (is_preset / id ==)."""

    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, statement):
        text = str(statement)
        if "= layout_templates.id" in text or "layout_templates.id =" in text:
            wanted = _bound_id(statement)
            return _FakeResult([r for r in self._rows if r.id == wanted])
        # is_preset == True branch (load all presets, ordered by id)
        preset_rows = sorted(
            (r for r in self._rows if r.is_preset), key=lambda r: r.id
        )
        return _FakeResult(preset_rows)


def _bound_id(statement):
    """Pull the bound id value out of a compiled SELECT ... WHERE id == :id."""
    return statement.compile().params.get("id_1")


def _select(content_type_id, rows=None):
    rows = _build_preset_rows() if rows is None else rows
    return asyncio.run(select_layout_template(content_type_id, _FakeSession(rows)))


# Mapping table the spec pins (Step-1 seed). Each content type -> the set of
# preset ids that legitimately serve it.
_SENSIBLE = {
    "product_showcase": {
        "lt_preset_product_spotlight",
        "lt_preset_split_demo",
        "lt_preset_ugc_review",
    },
    "tutorial": {"lt_preset_talking_head"},
    "general": {"lt_preset_talking_head"},
    "educational": {
        "lt_preset_voiceover_explainer",
        "lt_preset_educational_lecture",
    },
    "motion_creative": {"lt_preset_creative_motion"},
    "storytelling": {"lt_preset_story_vlog"},
    "fashion_lifestyle": {"lt_preset_fashion_lookbook"},
    "live_selling": {"lt_preset_live_selling"},
}

# Content types present in CONTENT_TYPES but not claimed by any preset's
# content_types list -> must fall through to the talking_head fallback.
_GAP_CONTENT_TYPES = {"entertainment", "cartoon_animation", "brand_awareness"}


def test_fallback_id_is_talking_head():
    assert FALLBACK_TEMPLATE_ID == "lt_preset_talking_head"


def test_every_content_type_is_classified():
    """Guard: every CONTENT_TYPES id is either in the sensible map or a known gap."""
    classified = set(_SENSIBLE) | _GAP_CONTENT_TYPES
    assert set(CONTENT_TYPES) == classified, (
        "content_type ids drifted from the Step-2 mapping table: "
        f"{set(CONTENT_TYPES) ^ classified}"
    )


@pytest.mark.parametrize("content_type_id", sorted(_SENSIBLE))
def test_mapped_content_types_return_sensible_template(content_type_id):
    tpl = _select(content_type_id)
    assert tpl.id in _SENSIBLE[content_type_id], (
        f"{content_type_id} -> {tpl.id}, expected one of "
        f"{sorted(_SENSIBLE[content_type_id])}"
    )


@pytest.mark.parametrize("content_type_id", sorted(_GAP_CONTENT_TYPES))
def test_unmapped_content_types_fall_back_to_talking_head(content_type_id):
    tpl = _select(content_type_id)
    assert tpl.id == "lt_preset_talking_head"


def test_unknown_id_falls_back_to_talking_head():
    assert _select("nonexistent_xyz").id == "lt_preset_talking_head"


def test_none_falls_back_to_talking_head():
    assert _select(None).id == "lt_preset_talking_head"


def test_duplicate_match_picks_first_by_id_asc():
    """When two presets claim the same content type, id-asc wins deterministically."""
    rows = _build_preset_rows()
    # split_demo and product_spotlight both already serve product_showcase;
    # id asc => product_spotlight (p < s).
    tpl = _select("product_showcase", rows=rows)
    product_ids = sorted(
        r.id for r in rows if "product_showcase" in r.config["content_types"]
    )
    assert tpl.id == product_ids[0] == "lt_preset_product_spotlight"


def test_all_ten_content_types_covered():
    """The 11 catalog ids resolve without raising; every result is a preset."""
    for content_type_id in CONTENT_TYPES:
        tpl = _select(content_type_id)
        assert tpl.id.startswith("lt_preset_")
