"""Product reviews endpoints — split from the former routers/products.py."""

import logging
import os
import uuid
import hashlib
from typing import Optional, List
from urllib.parse import quote
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from pydantic import BaseModel
import sentry_sdk
from database import get_db
from models.user import User, TeamRole
from models.product import Product
from models.product_asset import ProductAsset
from models.cast import Cast, CastStatus, CastProduct
from models.block import Block
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

class _ReviewItem(BaseModel):
    id: str
    stars: int
    author: str
    text: str
    verified: bool
    date: str

class GenerateReviewsResponse(BaseModel):
    reviews: List[_ReviewItem]
    generated_at: str
    model_used: str

def _strip_review_json_fences(raw: str) -> str:
    """Strip markdown fences and surrounding chatter from an LLM JSON reply."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
        cleaned = cleaned.strip()
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
    return cleaned

def _build_reviews_prompt(product: Product) -> str:
    desc = (product.description or "")[:300]
    return f"""Generate 25 realistic customer reviews for this product:

Product: {product.name}
Description: {desc}
Price: ${product.price}
Rating: {product.rating or 0}/5 ({product.review_count or 0} reviews)

Generate EXACTLY 5 reviews for each star rating (5 stars, 4 stars, 3 stars, 2 stars, 1 star).
Distribution should feel natural:
- 5-star reviews: enthusiastic, mention specific benefits
- 4-star reviews: positive but mention one minor issue
- 3-star reviews: balanced, "it's okay but..."
- 2-star reviews: disappointed, expected more
- 1-star reviews: negative but realistic (not abusive)

Each review needs:
- stars: integer 1-5
- author: first name + last initial, max 50 chars (e.g. "Sarah M.")
- text: 30-60 words, between 20 and 200 characters
- verified: boolean, ~80% true

Return JSON array only, no markdown:
[{{"stars": 5, "author": "Sarah M.", "text": "...", "verified": true}}, ...]"""

def _validate_reviews(parsed) -> list[dict]:
    """Validate raw review dicts; drop any that don't fit the contract."""
    if not isinstance(parsed, list):
        return []
    valid: list[dict] = []
    for r in parsed:
        if not isinstance(r, dict):
            continue
        try:
            stars = int(r.get("stars"))
        except (TypeError, ValueError):
            continue
        if stars < 1 or stars > 5:
            continue
        author = r.get("author")
        if not isinstance(author, str) or not author.strip():
            continue
        author = author.strip()[:50]
        text = r.get("text")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if len(text) < 20:
            continue
        if len(text) > 200:
            text = text[:200].rstrip()
        verified = r.get("verified", True)
        if not isinstance(verified, bool):
            verified = bool(verified)
        valid.append({
            "stars": stars,
            "author": author,
            "text": text,
            "verified": verified,
        })
    return valid

def _distribution_acceptable(reviews: list[dict]) -> bool:
    """5 per star ±1 — accept 4-6 per bucket."""
    counts = {s: 0 for s in range(1, 6)}
    for r in reviews:
        counts[r["stars"]] += 1
    return all(4 <= counts[s] <= 6 for s in range(1, 6))

async def _call_llm_for_reviews(prompt: str, system_prompt: str) -> str:
    from services.openrouter import get_openrouter_service
    return await get_openrouter_service().generate_text(
        prompt=prompt,
        system_prompt=system_prompt,
        model=_REVIEW_MODEL,
        max_tokens=2048,
        temperature=0.9,
    )

_REVIEW_GEN_RATE_LIMIT_SECONDS = 60

_review_gen_last_called: dict[tuple[str, str], float] = {}

_REVIEW_MODEL = "anthropic/claude-3-haiku"

@router.post("/{product_id}/generate-reviews", response_model=GenerateReviewsResponse)
async def generate_reviews(
    product_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate 25 sample reviews (5 per star bucket) via the cheap LLM.

    Reviews are ephemeral samples — not persisted. Rate-limited per (user,
    product) to one call per 60s as a cost guard.
    """
    import json
    import random
    import time
    from datetime import datetime, timedelta, timezone

    try:
        product = await db.get(Product, product_id)
        if not product or product.user_id != ctx.workspace_owner_id:
            raise HTTPException(404, "Product not found")

        rate_key = (user.id, product_id)
        now_ts = time.monotonic()
        last = _review_gen_last_called.get(rate_key)
        if last is not None and (now_ts - last) < _REVIEW_GEN_RATE_LIMIT_SECONDS:
            wait = int(_REVIEW_GEN_RATE_LIMIT_SECONDS - (now_ts - last))
            raise HTTPException(
                429,
                f"Sample reviews were just generated for this product. Try again in {wait}s.",
            )
        _review_gen_last_called[rate_key] = now_ts

        prompt = _build_reviews_prompt(product)
        system_prompt = "You write authentic product reviews. Return valid JSON only — no markdown, no explanation."

        # First call
        raw = await _call_llm_for_reviews(prompt, system_prompt)

        parsed = None
        try:
            parsed = json.loads(_strip_review_json_fences(raw))
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

        # JSON retry if parse failed
        if parsed is None:
            try:
                retry_prompt = (
                    "Your previous response was not valid JSON. Return ONLY a "
                    "valid JSON array of 25 review objects, no markdown, no "
                    "explanation.\n\n" + prompt
                )
                raw = await _call_llm_for_reviews(retry_prompt, system_prompt)
                parsed = json.loads(_strip_review_json_fences(raw))
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                raise HTTPException(
                    500,
                    "Couldn't generate reviews right now, please try again.",
                )

        reviews = _validate_reviews(parsed)

        # Regenerate once if too few survived validation or distribution skewed
        if len(reviews) < 15 or not _distribution_acceptable(reviews):
            try:
                raw = await _call_llm_for_reviews(prompt, system_prompt)
                parsed_retry = json.loads(_strip_review_json_fences(raw))
                retry_reviews = _validate_reviews(parsed_retry)
                if len(retry_reviews) > len(reviews):
                    reviews = retry_reviews
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                # Fall through with whatever we have

        if not reviews:
            raise HTTPException(
                500,
                "Couldn't generate reviews right now, please try again.",
            )

        # Add fake dates within last 6 months and ids
        now_dt = datetime.now(timezone.utc)
        for r in reviews:
            days_ago = random.randint(1, 180)
            r["date"] = (now_dt - timedelta(days=days_ago)).strftime("%b %d, %Y")
            r["id"] = f"rev_{uuid.uuid4().hex[:8]}"

        # 5-star first, then 4, etc.
        reviews.sort(key=lambda r: -r["stars"])

        # Cost tracking — best-effort, never fails the request
        try:
            from services.usage_logger import log_api_usage
            await log_api_usage(
                user_id=ctx.workspace_owner_id,                service="openrouter",
                operation="review_generation",
                success=True,
                cost_cents=0,  # ~$0.002 per call; refined once usage_tracker (PR #18) lands
            )
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

        return {
            "reviews": reviews,
            "generated_at": now_dt.isoformat(),
            "model_used": _REVIEW_MODEL,
        }
    except HTTPException:
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            500,
            "Couldn't generate reviews right now, please try again.",
        )
