"""
Chat Message Classifier — 3-tier routing pipeline.
Tier 1: Keyword matching (instant)
Tier 2: Product-specific Q&A rules (instant)
Tier 3: LLM fallback (1-3 seconds)
Plus safety filters and purchase detection.
"""
import re
import logging
import json
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Tier 1: Keyword → response mapping
KEYWORD_RESPONSES: dict[str, str] = {
    "shipping": "Shipping is free on all orders! 🚚",
    "ship": "Shipping is free on all orders! 🚚",
    "delivery": "Standard delivery takes 3-5 business days! 📦",
    "price": "Check the product link below for current pricing! 💰",
    "cost": "Check the product link below for current pricing! 💰",
    "how much": "Check the product link below for current pricing! 💰",
    "discount": "Tap the basket below for the best deal available right now! 🔥",
    "coupon": "Check the product link — special live stream pricing may be available! 🎉",
    "size": "Size details are in the product listing — tap the basket to check! 📏",
    "sizing": "Size details are in the product listing — tap the basket to check! 📏",
    "return": "Returns accepted within 30 days — check seller's return policy for details! ✅",
    "refund": "Returns accepted within 30 days — check seller's return policy for details! ✅",
    "color": "Available colors are shown in the product listing — tap the basket! 🎨",
    "ingredient": "Full ingredient list is in the product details! Check the link below 📋",
    "ingredients": "Full ingredient list is in the product details! Check the link below 📋",
}

# Safety: words that should never appear in responses
BANNED_RESPONSE_WORDS = {"cures", "treats", "heals", "diagnoses", "medical", "prescription"}

# Abuse detection patterns
ABUSE_PATTERNS = [
    re.compile(r'\b(fuck|shit|damn|bitch|ass|dick|cunt)\b', re.IGNORECASE),
    re.compile(r'\b(hate|kill|die|stupid|idiot|ugly|loser)\b', re.IGNORECASE),
    re.compile(r'\b(scam|fake|fraud|ripoff|rip.off)\b', re.IGNORECASE),
]

# Purchase detection patterns
PURCHASE_PATTERNS = [
    re.compile(r'🛒.*bought', re.IGNORECASE),
    re.compile(r'just (bought|purchased|ordered)', re.IGNORECASE),
    re.compile(r'(bought|purchased) .*!', re.IGNORECASE),
]

MAX_RESPONSE_LENGTH = 280


@dataclass
class ClassificationResult:
    tier: int  # 1, 2, or 3
    response: Optional[str]
    is_purchase: bool
    is_abusive: bool
    matched_keyword: Optional[str] = None
    matched_product_rule: Optional[str] = None
    needs_approval: bool = False


def is_purchase_event(message: str) -> bool:
    """Detect if message is a purchase event notification."""
    return any(p.search(message) for p in PURCHASE_PATTERNS)


def is_abusive(message: str) -> bool:
    """Detect abusive or hateful messages."""
    return any(p.search(message) for p in ABUSE_PATTERNS)


def check_safety(response: str) -> str:
    """Ensure response passes safety checks. Truncate if too long. Remove banned words."""
    # Truncate
    if len(response) > MAX_RESPONSE_LENGTH:
        response = response[:MAX_RESPONSE_LENGTH - 3] + "..."

    # Remove banned words
    for word in BANNED_RESPONSE_WORDS:
        response = re.sub(rf'\b{word}\b', '***', response, flags=re.IGNORECASE)

    return response


def classify_tier1(message: str) -> Optional[str]:
    """Tier 1: Keyword matching. Returns response or None."""
    message_lower = message.lower()
    for keyword, response in KEYWORD_RESPONSES.items():
        if keyword in message_lower:
            return check_safety(response)
    return None


def classify_tier2(message: str, product_rules: list[dict]) -> Optional[str]:
    """
    Tier 2: Product-specific Q&A rules.
    product_rules: [{"keywords": ["oily", "skin type"], "response": "Yes! Our serum is oil-free..."}]
    Returns response or None.
    """
    message_lower = message.lower()
    for rule in product_rules:
        keywords = rule.get("keywords", [])
        if any(kw.lower() in message_lower for kw in keywords):
            response = rule.get("response", "")
            return check_safety(response)
    return None


async def classify_tier3(message: str, context: dict) -> str:
    """
    Tier 3: LLM fallback for complex questions.
    context: {product_info, persona_profile, recent_messages}
    Returns AI-generated response.
    """
    from services.openrouter import get_openrouter_service

    prompt = f"""You are a helpful TikTok live stream chat assistant.

Current product: {json.dumps(context.get('product_info', {}))}
Creator persona: {json.dumps(context.get('persona_profile', {}))}
Recent chat: {json.dumps(context.get('recent_messages', [])[-20:])}

Viewer question: "{message}"

Rules:
- Response MUST be under 280 characters
- Match the creator's speaking style
- Never make medical claims (no "cures", "treats", "heals")
- Be friendly and helpful
- If you can't answer, say "Great question! Check the product link for details 😊"

Reply:"""

    from services.ai_prompts import get_prompt
    chat_prompt = get_prompt("chat_classifier")
    response = await get_openrouter_service().generate_text(
        prompt=prompt,
        system_prompt=chat_prompt["system"],
        max_tokens=100,
        temperature=0.7,
    )

    return check_safety(response.strip())


async def classify_message(
    message: str,
    product_rules: list[dict] | None = None,
    context: dict | None = None,
    auto_send: bool = False,
) -> ClassificationResult:
    """
    Full classification pipeline.
    Returns ClassificationResult with tier, response, and metadata.
    """
    # Check for purchase event
    if is_purchase_event(message):
        return ClassificationResult(
            tier=0,
            response=None,
            is_purchase=True,
            is_abusive=False,
        )

    # Check for abuse
    if is_abusive(message):
        return ClassificationResult(
            tier=0,
            response=None,
            is_purchase=False,
            is_abusive=True,
        )

    # Tier 1: Keyword matching
    tier1_response = classify_tier1(message)
    if tier1_response:
        return ClassificationResult(
            tier=1,
            response=tier1_response,
            is_purchase=False,
            is_abusive=False,
            matched_keyword=message.lower(),
        )

    # Tier 2: Product rules
    if product_rules:
        tier2_response = classify_tier2(message, product_rules)
        if tier2_response:
            return ClassificationResult(
                tier=2,
                response=tier2_response,
                is_purchase=False,
                is_abusive=False,
                matched_product_rule="product_rule",
            )

    # Tier 3: LLM fallback
    if context:
        tier3_response = await classify_tier3(message, context)
        return ClassificationResult(
            tier=3,
            response=tier3_response,
            is_purchase=False,
            is_abusive=False,
            needs_approval=not auto_send,
        )

    return ClassificationResult(
        tier=3,
        response=None,
        is_purchase=False,
        is_abusive=False,
        needs_approval=True,
    )
