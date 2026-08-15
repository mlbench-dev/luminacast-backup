"""Cast Builder — CRUD, blocks, variants, script generation, Cast generation.

Split from a single 6877-line routers/casts.py into per-concern modules.
This file assembles all of them under the same /api/casts prefix/tags so
`from routers import casts; app.include_router(casts.router)` in main.py
needs no changes.

The prefix is applied per include_router() call (not on this router's own
constructor) because crud.py has routes at the bare root path ("" — e.g.
create_cast/list_casts). FastAPI's include_router() skips its "prefix and
path cannot both be empty" validation only when a non-empty `prefix` is
passed to that specific call; a prefix set on this router's own
constructor doesn't count for that check, since it's applied later, when
this router itself is mounted into the app.
"""
from fastapi import APIRouter

from . import crud, blocks, frames, variants, generation, tts_captions, timeline, clips, render, forking

router = APIRouter(tags=["casts"])

router.include_router(crud.router, prefix="/api/casts")
router.include_router(blocks.router, prefix="/api/casts")
router.include_router(frames.router, prefix="/api/casts")
router.include_router(variants.router, prefix="/api/casts")
router.include_router(generation.router, prefix="/api/casts")
router.include_router(tts_captions.router, prefix="/api/casts")
router.include_router(timeline.router, prefix="/api/casts")
router.include_router(clips.router, prefix="/api/casts")
router.include_router(render.router, prefix="/api/casts")
router.include_router(forking.router, prefix="/api/casts")
