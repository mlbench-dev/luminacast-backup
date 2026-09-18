"""B-roll pipeline debug playground — admin-only.

Given a cast prompt + a product, runs the REAL outline generation and
script generation (the exact same functions a real cast uses —
``engine.cast_generator.generate_outline`` / ``generate_scripts``, no real
Cast/Block DB rows involved, both are pure LLM-orchestration functions), then
for every block that would get B-roll, runs the exact query-candidate
resolution (``resolve_block_query_candidates``, the same function
``auto_populate_stock_media`` calls) and instruments every step of Pexels
search + selection. So the whole "what does the AI decide for the script,
what gets sent to Pexels for each block, what comes back, which clip wins
and why" chain is visible instead of a black box. Nothing here touches real
casts/blocks/billing.

  POST /api/dev/broll-playground/run
    {product_id, prompt, duration_target_seconds?, orientation?}
    -> {product, outline_prompt, blocks: [{block info, script_text,
        broll: <trace> | null}]}
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from models.user import User
from routers.auth import require_admin

router = APIRouter(prefix="/api/dev/broll-playground", tags=["dev-playground"])


class RunRequest(BaseModel):
    product_id: str
    prompt: str
    duration_target_seconds: int = 30
    orientation: str = "portrait"


def _wants_photo(category: str, background_type: Optional[str]) -> bool:
    # Exact condition _fetch_for_block uses to pick the search endpoint.
    return category == "stock_photo" or background_type == "stock_photo"


def _gets_broll(category: str) -> bool:
    # Approximates _fetch_for_block's skip rule (category == "avatar_speaking"
    # and render_mode == "avatar_full"). render_mode doesn't exist yet at
    # outline-generation time (it's assigned from category when Block rows
    # are created) — per the routing taxonomy, avatar_speaking always maps
    # to avatar_full render_mode by default, so checking category alone is
    # an accurate approximation for every real-world case.
    return category != "avatar_speaking"


def _summarize_video(v: dict) -> dict:
    best_file = None
    for vf in v.get("video_files", []) or []:
        if vf.get("quality") == "hd" and vf.get("file_type") == "video/mp4":
            best_file = vf
            break
    if not best_file and v.get("video_files"):
        best_file = v["video_files"][0]
    return {
        "id": v.get("id"),
        "kind": "video",
        "pexels_page_url": v.get("url"),
        "thumbnail": v.get("image"),
        "width": v.get("width"),
        "height": v.get("height"),
        "duration": v.get("duration"),
        "photographer": (v.get("user") or {}).get("name"),
        "play_url": (best_file or {}).get("link"),
        # The complete, unmodified object Pexels returned for this
        # candidate — every field (video_files variants, video_pictures,
        # tags, user object, avg_color, etc.), not just the ones the
        # summary above picks out.
        "raw": v,
    }


def _summarize_photo(p: dict) -> dict:
    src = p.get("src") or {}
    return {
        "id": p.get("id"),
        "kind": "photo",
        "pexels_page_url": p.get("url"),
        "thumbnail": src.get("tiny") or src.get("medium"),
        "width": p.get("width"),
        "height": p.get("height"),
        "photographer": p.get("photographer"),
        "play_url": src.get("large2x") or src.get("original"),
        "raw": p,
    }


async def _trace_block_broll(client, block: dict, product_dict: dict, orientation: str) -> dict:
    """Run + instrument the full query-candidate ladder + Pexels search for
    ONE block. Same logic previously inlined directly in the endpoint —
    factored out so it can run once per block in a loop.
    """
    from engine.cast_generator import (
        resolve_block_query_candidates,
        _block_beat_text,
        _broll_rerank_enabled,
        _broll_candidate_count,
        _broll_finalist_count,
        _prefilter_video_candidates,
        _vision_pick_video,
    )

    queries = await resolve_block_query_candidates(block, product_dict)
    beat_text = _block_beat_text(block, "")

    wants_photo = _wants_photo(block.get("category") or "", block.get("background_type"))
    rerank_on = _broll_rerank_enabled()
    per_page = 3 if wants_photo else (_broll_candidate_count() if rerank_on else 3)

    query_attempts: list[dict] = []
    winner_query: Optional[str] = None
    winner_media: Optional[dict] = None

    for q in queries:
        q = (q or "").strip()
        if not q:
            continue

        if wants_photo:
            results = await client.safe_search_photos(
                q, per_page=per_page, orientation=orientation,
            )
            photos = (results or {}).get("photos") or []
            attempt: dict = {
                "query": q,
                "raw_result_count": len(photos),
                "raw_candidates": [_summarize_photo(p) for p in photos[:12]],
                "rerank_enabled": False,
                "finalists": None,
                "vision_pick_index": None,
                "chosen": None,
                "outcome": "no_results",
            }
            if photos:
                chosen = _summarize_photo(photos[0])
                if not chosen.get("play_url"):
                    attempt["outcome"] = "rejected — no large2x src on top result"
                else:
                    attempt["chosen"] = chosen
                    attempt["outcome"] = "won — first photo result (stock_photo never reranks)"
                    if winner_query is None:
                        winner_query = q
                        winner_media = chosen
            query_attempts.append(attempt)
            continue

        results = await client.safe_search_videos(
            q, per_page=per_page, orientation=orientation,
        )
        videos = (results or {}).get("videos") or []

        attempt = {
            "query": q,
            "raw_result_count": len(videos),
            "raw_candidates": [_summarize_video(v) for v in videos[:12]],
            "rerank_enabled": rerank_on,
            "finalists": None,
            "vision_pick_index": None,
            "chosen": None,
            "outcome": "no_results",
        }

        if videos:
            if not rerank_on or len(videos) <= 1:
                chosen_v = videos[0]
                attempt["outcome"] = "won — rerank skipped (disabled or only 1 result)"
            else:
                try:
                    finalists = _prefilter_video_candidates(
                        videos, q, orientation, _broll_finalist_count(),
                    )
                except Exception as exc:
                    finalists = []
                    attempt["prefilter_error"] = str(exc)[:300]
                attempt["finalists"] = [_summarize_video(v) for v in finalists]
                if not finalists:
                    chosen_v = videos[0]
                    attempt["outcome"] = "won — prefilter returned nothing, used raw first result"
                else:
                    idx = await _vision_pick_video(
                        finalists, beat_text, q, "playground", 0,
                    )
                    attempt["vision_pick_index"] = idx
                    chosen_v = finalists[idx]
                    attempt["outcome"] = f"won — vision model picked finalist #{idx}"
            attempt["chosen"] = _summarize_video(chosen_v)
            if winner_query is None:
                winner_query = q
                winner_media = attempt["chosen"]

        query_attempts.append(attempt)

    return {
        "searched_catalog": "photo" if wants_photo else "video",
        "block_beat_text": beat_text,
        "query_candidates_in_order": queries,
        "query_attempts": query_attempts,
        "final_pick": (
            {"query": winner_query, "media": winner_media}
            if winner_query else None
        ),
    }


@router.post("/run")
async def run_broll_playground(req: RunRequest, user: User = Depends(require_admin)):
    from database import async_session_factory
    from models.product import Product
    from services.pexels import get_pexels_client_optional
    from services.r2_storage import get_r2_storage_service
    from services.product_stock_queries import (
        get_or_generate_product_ai_stock_queries,
        get_or_generate_product_visual_description,
    )
    from engine.cast_generator import generate_outline, generate_scripts

    client = get_pexels_client_optional()
    if client is None:
        raise HTTPException(503, "Pexels not configured on this server")

    r2 = get_r2_storage_service()

    async with async_session_factory() as db:
        product_row = await db.get(Product, req.product_id)
        if not product_row:
            raise HTTPException(404, "Product not found")

        ai_qs = await get_or_generate_product_ai_stock_queries(product_row, db, r2)
        ai_desc = await get_or_generate_product_visual_description(product_row, db, r2)

    # Same shape generate_outline/generate_scripts read (see generation.py's
    # real endpoints) plus the AI vision fields resolve_block_query_candidates
    # reads.
    product_dict = {
        "id": product_row.id,
        "name": product_row.name,
        "price": product_row.price,
        "description": product_row.description or "",
        "key_benefits": getattr(product_row, "key_benefits", None) or [],
        "category": getattr(product_row, "category", "") or "",
        "ai_stock_queries": ai_qs,
        "ai_visual_description": ai_desc,
    }

    playground_cast_id = f"playground_{uuid.uuid4().hex[:8]}"

    # Step 1: the REAL outline generator — no real Cast/Block rows involved,
    # generate_outline is a pure LLM-orchestration function.
    try:
        outline = await generate_outline(
            playground_cast_id, [product_dict], {}, "custom",
            description=req.prompt,
            duration_target_seconds=req.duration_target_seconds,
            user_id=user.id,
        )
    except Exception as exc:
        raise HTTPException(502, f"Outline generation failed: {str(exc)[:300]}")
    if not outline:
        raise HTTPException(502, "Outline generation returned no blocks")

    # Step 2: the REAL script generator on those exact blocks.
    try:
        scripts = await generate_scripts(
            playground_cast_id, outline, {}, description=req.prompt,
            products=[product_dict], user_id=user.id,
        )
    except Exception as exc:
        raise HTTPException(502, f"Script generation failed: {str(exc)[:300]}")

    script_by_index = {s["block_index"]: s for s in scripts}
    for i, block in enumerate(outline):
        s = script_by_index.get(i) or {}
        block["script_text"] = s.get("script_text", "")

    # Step 3: per-block B-roll trace for every block that would get one.
    blocks_out = []
    for i, block in enumerate(outline):
        category = block.get("category") or "avatar_speaking"
        gets_broll = _gets_broll(category)
        entry = {
            "index": i,
            "block_type": block.get("block_type"),
            "category": category,
            "key_points": block.get("key_points"),
            "stock_media_query": block.get("stock_media_query"),
            "visual_subject": block.get("visual_subject"),
            "script_text": block.get("script_text", ""),
            "gets_broll": gets_broll,
            "broll": None,
        }
        if gets_broll:
            entry["broll"] = await _trace_block_broll(
                client, block, product_dict, req.orientation,
            )
        blocks_out.append(entry)

    return {
        "input": {
            "product_id": req.product_id,
            "prompt": req.prompt,
            "duration_target_seconds": req.duration_target_seconds,
            "orientation": req.orientation,
        },
        "product": {
            "name": product_row.name,
            "cover_image_url": (
                r2.get_public_url(product_row.cover_image_key)
                if product_row.cover_image_key else None
            ),
            "ai_stock_queries": ai_qs,
            "ai_visual_description": ai_desc,
        },
        "blocks": blocks_out,
    }
