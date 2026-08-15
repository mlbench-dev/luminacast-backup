"""Products — CRUD, assets, TikTok Shop import, AI media, reviews.

Split from a single 1826-line routers/products.py into per-concern
modules. This file assembles all of them under the same /api/products
prefix/tags so `from routers import products; app.include_router(products.router)`
in main.py needs no changes.
"""
from fastapi import APIRouter

from . import crud, assets, tiktok, ai_media, reviews

router = APIRouter(prefix="/api/products", tags=["products"])

router.include_router(crud.router)
router.include_router(assets.router)
router.include_router(tiktok.router)
router.include_router(ai_media.router)
router.include_router(reviews.router)
