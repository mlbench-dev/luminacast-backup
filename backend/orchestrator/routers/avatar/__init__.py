"""Avatar — clone pipeline, AI avatar generation, voice, style DNA, body shots.

Split from a single 5324-line routers/avatar.py into per-concern modules,
plus _shared.py for the handful of helpers/classes used across more than
one of them (AvatarResponse / _avatar_to_response chief among them). This
file assembles all of them under the same /api/avatar prefix/tags so
`from routers import avatar; app.include_router(avatar.router)` in main.py
needs no changes.
"""
from fastapi import APIRouter

from . import pipeline, video_fetch, style_dna, ai_faces, ai_voice, ai_generation, body_shots, render_pipeline

router = APIRouter(prefix="/api/avatar", tags=["avatar"])

router.include_router(pipeline.router)
router.include_router(video_fetch.router)
router.include_router(style_dna.router)
router.include_router(ai_faces.router)
router.include_router(ai_voice.router)
router.include_router(ai_generation.router)
router.include_router(body_shots.router)
router.include_router(render_pipeline.router)
