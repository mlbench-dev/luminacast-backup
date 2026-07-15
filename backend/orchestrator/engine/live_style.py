"""Resolve + render the active Live Style Assessment for the script prompt.

Used by engine.cast_generator to condition outline generation on a creator's
past live sessions. The assessment supplies cadence / structure / disfluency
texture and selling patterns — it MUST NOT override the avatar's persona (the
reconciliation rules in the prompt enforce that ordering).

No embedding infra exists on this deploy, so select_exemplars ranks by token
overlap against the product+brief text. When an embedding column is populated
later, the cosine branch activates automatically.
"""
from __future__ import annotations

import logging
import math
import os
import re
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)

_MERGE_PRECEDENCE_FIELDS = ("exemplar_bank", "register", "cta")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _shots_default() -> int:
    return int(os.environ.get("LIVE_REF_SHOTS", "6"))


async def resolve_active_assessment(
    session, cast_id: Optional[str], avatar_id: Optional[str]
) -> Optional[dict]:
    """Return the active assessment dict, or None.

    Cast-level reference (most-recent assessed LiveReference for this cast)
    wins; otherwise the avatar's most-recent assessed reference. When both
    exist they are merged — cast-level takes precedence on exemplar_bank,
    register, and cta; the avatar's fills any missing fields.
    """
    from models.live_reference import LiveReference
    from sqlalchemy import select

    async def _latest(field, value) -> Optional[dict]:
        if not value:
            return None
        try:
            stmt = (
                select(LiveReference)
                .where(field == value)
                .where(LiveReference.status == "assessed")
                .order_by(LiveReference.created_at.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).scalars().first()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return None
        return row.assessment if row and row.assessment else None

    cast_assessment = await _latest(LiveReference.cast_id, cast_id)
    avatar_assessment = await _latest(LiveReference.avatar_id, avatar_id)

    if cast_assessment and avatar_assessment:
        return _merge(cast_assessment, avatar_assessment)
    return cast_assessment or avatar_assessment


def _merge(primary: dict, secondary: dict) -> dict:
    """Cast-level (primary) wins on precedence fields; avatar (secondary) fills
    any field the primary is missing/empty."""
    merged = dict(secondary)
    for key, value in primary.items():
        if key in _MERGE_PRECEDENCE_FIELDS:
            merged[key] = value
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 3}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def select_exemplars(
    assessment: Optional[dict],
    product_description: str,
    brief: str,
    top_k: Optional[int] = None,
) -> list[dict]:
    """Pick the top_k most relevant exemplars from the assessment's bank.

    If exemplars carry embeddings, rank by cosine similarity against the
    product+brief embedding (when a query embedding is available). Otherwise
    rank by token overlap with the product+brief text. Ties keep original
    order so a no-signal query returns the first top_k deterministically.
    """
    if not assessment:
        return []
    bank = assessment.get("exemplar_bank") or []
    if not bank:
        return []
    k = top_k if top_k is not None else _shots_default()
    query_text = f"{product_description}\n{brief}"
    query_tokens = _tokens(query_text)

    scored: list[tuple[float, int, dict]] = []
    for idx, ex in enumerate(bank):
        emb = ex.get("embedding") if isinstance(ex, dict) else None
        if emb and assessment.get("_query_embedding"):
            score = _cosine(assessment["_query_embedding"], emb)
        else:
            score = float(len(query_tokens & _tokens(ex.get("text", "")))) if query_tokens else 0.0
        scored.append((score, idx, ex))

    # sort by score desc, then original index asc (stable, deterministic)
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [ex for _, _, ex in scored[:k]]


# Literal reconciliation guard — injected into every outline prompt whether or
# not an assessment is found, so the AVATAR > PRODUCT > BRIEF ordering is always
# stated to the model.
RECONCILIATION_RULES = """RECONCILIATION RULES — apply in this order:
1. The AVATAR description above governs personality. The live reference supplies cadence, structure, disfluency texture, and selling patterns — it MUST NOT override the avatar's described persona. A calm avatar stays calm even if the live reference is high-energy.
2. The PRODUCT description focuses pattern choice. Pull objection-handling, price-framing, and cross-sell patterns from the live reference ONLY where they fit this product; drop irrelevant ones.
3. The USER GOAL / brief sets the beat. Hook beats pull the hook exemplars, demo beats pull the demo exemplars, CTA beats pull the CTA exemplars."""


def build_live_style_section(
    assessment: Optional[dict], selected_exemplars: list[dict]
) -> str:
    """Render the LIVE STYLE REFERENCE prompt section + reconciliation rules.

    When no assessment is found, only the reconciliation rules are emitted (the
    rules still reference the live reference so the model's ordering is stable).
    """
    if not assessment:
        return RECONCILIATION_RULES + "\n"

    disfluency = assessment.get("disfluency_profile") or {}
    fillers = ", ".join(disfluency.get("fillers") or []) or "none noted"
    self_corr = disfluency.get("self_correction") or "n/a"
    cta = assessment.get("cta") or {}
    cadence = cta.get("cadence_min") or []
    cadence_str = "-".join(str(c) for c in cadence) if cadence else "n/a"
    phrasings = (cta.get("phrasings") or [])[:3]

    lines = [
        "LIVE STYLE REFERENCE (cadence/structure/disfluency — do NOT override persona):",
        f"Register: {assessment.get('register', '')}",
        f"Disfluency profile: fillers={fillers}, self_correction={self_corr}",
        f"CTA cadence (minutes between CTAs): {cadence_str}",
        f"CTA phrasings: {'; '.join(phrasings) if phrasings else 'n/a'}",
    ]
    if selected_exemplars:
        lines.append("Selected exemplars (use as inspiration for cadence/turns, NOT verbatim):")
        for ex in selected_exemplars:
            beat = ex.get("beat", "hook")
            text = (ex.get("text") or "").strip()
            lines.append(f'- [{beat}] "{text}"')
    lines.append("")
    lines.append(RECONCILIATION_RULES)
    return "\n".join(lines) + "\n"
