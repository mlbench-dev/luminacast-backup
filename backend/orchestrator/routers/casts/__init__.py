"""Cast Builder — CRUD, blocks, variants, script generation, Cast generation.

Split from a single 6877-line routers/casts.py into per-concern modules.
This file assembles all of them under the same /api/casts prefix/tags so
`from routers import casts; app.include_router(casts.router)` in main.py
needs no changes.
"""
from fastapi import APIRouter

from . import crud, blocks, frames, variants, generation, tts_captions, timeline, clips, render, forking

router = APIRouter(prefix="/api/casts", tags=["casts"])

router.include_router(crud.router)
router.include_router(blocks.router)
router.include_router(frames.router)
router.include_router(variants.router)
router.include_router(generation.router)
router.include_router(tts_captions.router)
router.include_router(timeline.router)
router.include_router(clips.router)
router.include_router(render.router)
router.include_router(forking.router)
