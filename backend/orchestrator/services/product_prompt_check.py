"""Pre-flight check: does a cast's creative direction name a specific,
different product that conflicts with what's actually attached to it?

Catches the "typed the wrong product name" mistake (direction says
"chopping board", attached product is a lazy susan) BEFORE script
generation runs — cheaply, using a single fast/cheap LLM call, since a
generation run that goes ahead on a contradictory brief either drifts the
whole script off-product or produces a confused blend of both.

Deliberately does NOT flag generic scene/activity/emotion directions
("show a man waking up in the morning") that don't name a product at all
— those are fine even though they don't mention the product by name; the
script-generation prompt's own product-grounding instructions are what
keep those honest, not this check. This check only exists for the
narrower, sharper case: the direction itself names a conflicting object.
"""
import json
import logging

logger = logging.getLogger(__name__)


async def check_product_prompt_mismatch(
    description: str | None,
    products: list[dict] | None,
) -> dict:
    """Returns ``{"mismatch": bool, "named_object": str | None}``.

    Best-effort: any failure (LLM error, bad JSON, no API key) returns
    ``{"mismatch": False, "named_object": None}`` so this can never block
    a generation it failed to actually check.
    """
    if not description or not description.strip() or not products:
        return {"mismatch": False, "named_object": None}

    product_lines = "\n".join(
        f"- {p.get('name', 'Unknown')}: {(p.get('description') or '')[:200]}"
        for p in products
        if p.get("name")
    )
    if not product_lines:
        return {"mismatch": False, "named_object": None}

    prompt = f"""A user is creating a short video ad. Their creative direction is:
"{description}"

The actual product(s) attached to this ad:
{product_lines}

Does the creative direction explicitly name a SPECIFIC, DIFFERENT physical product or object category that conflicts with the attached product(s) above? Example: direction says "chopping board" but the attached product is a "lazy susan turntable" — that's a conflict.

Do NOT flag a mismatch just because the direction is a generic scene, activity, or emotion that doesn't name any product at all (e.g. "show a man waking up in the morning", "show a woman feeling confident", "a cozy evening at home") — those are FINE even though they don't mention the product by name. Only flag when the direction itself explicitly names a specific, different product/object.

Respond with ONLY this JSON object, no other text, no markdown fence:
{{"mismatch": true or false, "named_object": "the conflicting object mentioned in the direction, or null"}}"""

    try:
        from services.openrouter import get_openrouter_service

        client = get_openrouter_service()
        raw = await client.generate_text(
            prompt,
            model="anthropic/claude-3-haiku",
            max_tokens=150,
            temperature=0,
        )
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        return {
            "mismatch": bool(data.get("mismatch")),
            "named_object": (data.get("named_object") or None),
        }
    except Exception as e:
        logger.warning("product-prompt mismatch check failed (non-fatal): %s", e)
        return {"mismatch": False, "named_object": None}
