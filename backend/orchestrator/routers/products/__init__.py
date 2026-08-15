"""Products — CRUD, assets, TikTok Shop import, AI media, reviews.

Split from a single 1826-line routers/products.py into per-concern
modules. This file assembles all of them under the same /api/products
prefix/tags so `from routers import products; app.include_router(products.router)`
in main.py needs no changes.

The prefix is applied per include_router() call (not on this router's own
constructor) because crud.py has routes at the bare root path ("" — e.g.
create_product/list_products). FastAPI's include_router() skips its
"prefix and path cannot both be empty" validation only when a non-empty
`prefix` is passed to that specific call; a prefix set on this router's
own constructor doesn't count for that check, since it's applied later,
when this router itself is mounted into the app.
"""
from fastapi import APIRouter

from . import crud, assets, tiktok, ai_media, reviews

router = APIRouter(tags=["products"])

router.include_router(crud.router, prefix="/api/products")
router.include_router(assets.router, prefix="/api/products")
router.include_router(tiktok.router, prefix="/api/products")
router.include_router(ai_media.router, prefix="/api/products")
router.include_router(reviews.router, prefix="/api/products")
