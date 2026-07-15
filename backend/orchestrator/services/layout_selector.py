"""Step 2 — map a detected content type to a layout-template preset.

Pure, DB-read-only selection logic. No mutations, no side effects beyond the
single read of the ``layout_templates`` table. Not yet wired into generation
(that is Step 3).

The mapping is data-driven: each preset row seeded in Step 1 carries a
``config.content_types`` list of ``detect_content_type`` ids it serves
(see ``scripts/seed_layout_templates.py``). ``select_layout_template`` finds the
preset whose list contains the requested id and returns that row, falling back
to the Talking Head preset whenever nothing matches (unknown id, ``None``, or a
content type no preset claims).
"""
from __future__ import annotations

import logging
import os

import sentry_sdk
from sqlalchemy import select

from models.layout_template import LayoutTemplate

logger = logging.getLogger(__name__)

# The fallback preset id is env-overridable so the safety net can be retargeted
# without a code change (no hardcoded behaviour locked in).
FALLBACK_TEMPLATE_ID = os.getenv("LAYOUT_SELECTOR_FALLBACK_ID", "lt_preset_talking_head")


async def select_layout_template(content_type_id: str | None, db) -> LayoutTemplate:
    """Pick the layout-template preset that serves ``content_type_id``.

    Looks up the preset row whose ``config.content_types`` contains
    ``content_type_id``. Falls back to the Talking Head preset when nothing
    matches or ``content_type_id`` is unknown / ``None``.

    If more than one preset claims the same content type, the first by
    deterministic ordering (``id`` ascending) wins and a warning is logged.

    Pure / DB-read only: issues a single SELECT and returns the chosen row.
    Any exception path returns the fallback preset (Sentry-captured).
    """
    try:
        presets = await _load_presets(db)

        matches = [
            tpl
            for tpl in presets.values()
            if content_type_id and content_type_id in _content_types_of(tpl)
        ]
        if not matches:
            return _fallback(presets)

        matches.sort(key=lambda tpl: tpl.id)
        if len(matches) > 1:
            logger.warning(
                "content_type %r maps to %d presets %s; choosing %r (id asc)",
                content_type_id,
                len(matches),
                [tpl.id for tpl in matches],
                matches[0].id,
            )
        return matches[0]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "layout selection failed for content_type %r, using fallback %r: %s",
            content_type_id,
            FALLBACK_TEMPLATE_ID,
            e,
        )
        return await _load_fallback(db)


async def _load_presets(db) -> dict[str, LayoutTemplate]:
    """Read every preset row, keyed by id (single SELECT, read-only)."""
    result = await db.execute(
        select(LayoutTemplate)
        .where(LayoutTemplate.is_preset == True)  # noqa: E712
        .order_by(LayoutTemplate.id)
    )
    return {tpl.id: tpl for tpl in result.scalars().all()}


async def _load_fallback(db) -> LayoutTemplate:
    """Load only the fallback preset (used on the exception path)."""
    result = await db.execute(
        select(LayoutTemplate).where(LayoutTemplate.id == FALLBACK_TEMPLATE_ID)
    )
    tpl = result.scalars().first()
    if tpl is None:
        raise LookupError(f"fallback preset {FALLBACK_TEMPLATE_ID!r} not seeded")
    return tpl


def _fallback(presets: dict[str, LayoutTemplate]) -> LayoutTemplate:
    tpl = presets.get(FALLBACK_TEMPLATE_ID)
    if tpl is None:
        raise LookupError(f"fallback preset {FALLBACK_TEMPLATE_ID!r} not seeded")
    return tpl


def _content_types_of(tpl: LayoutTemplate) -> list[str]:
    config = tpl.config or {}
    types = config.get("content_types")
    return types if isinstance(types, list) else []
