"""Celery task for generating acting video clips via Kling."""
import logging
import uuid

import httpx
import sentry_sdk

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="generate_acting_video", task_time_limit=600)
def generate_acting_video_task(self, block_id: str, user_id: str = None):
    """Generate a 5-second acting video for a block using Kling 2.5 Turbo Pro.

    task_time_limit=600 is the dead-end failsafe (10 minutes).
    """
    import asyncio

    async def _run():
        from database import async_session_maker
        from models.block import Block
        from models.avatar import Avatar, BodyShotSet
        from models.cast import Cast
        from models.render_job import RenderJob, RenderJobState, RenderJobType, RenderProvider
        from models.generation_cost import GenerationCost
        from services.acting_video_client import ActingVideoClient
        from services.r2_storage import get_r2_storage_service
        from config import settings

        r2 = get_r2_storage_service()

        async with async_session_maker() as session:
            block = await session.get(Block, block_id)
            if not block:
                logger.error("Acting video task: block %s not found", block_id)
                return

            if block.block_type != "acting":
                logger.error("Block %s is not an acting block (type=%s)", block_id, block.block_type)
                return

            cast = await session.get(Cast, block.cast_id)
            if not cast:
                logger.error("Cast %s not found for block %s", block.cast_id, block_id)
                return

            avatar = await session.get(Avatar, cast.avatar_id)
            if not avatar:
                logger.error("Avatar %s not found for cast %s", cast.avatar_id, cast.id)
                return

            # Create render job
            rj_id = f"rj_{uuid.uuid4().hex[:16]}"
            rj = RenderJob(
                id=rj_id,
                job_type=RenderJobType.ACTING_VIDEO_GENERATION,
                provider=RenderProvider.FAL_KLING,
                avatar_id=avatar.id,
                cast_id=cast.id,
                variant_id=None,
                state=RenderJobState.IN_PROGRESS,
            )
            session.add(rj)
            await session.commit()

            try:
                # Get body shot URLs for first/last frame angles
                first_frame_url = None
                last_frame_url = None

                if block.acting_first_frame_angle:
                    first_frame_url = await _get_body_shot_url(
                        session, avatar, block.acting_first_frame_angle, r2
                    )
                if block.acting_last_frame_angle:
                    last_frame_url = await _get_body_shot_url(
                        session, avatar, block.acting_last_frame_angle, r2
                    )

                # Fallback to face_ref if no body shot
                if not first_frame_url and avatar.face_ref_key:
                    first_frame_url = r2.get_public_url(avatar.face_ref_key)

                if not first_frame_url:
                    raise RuntimeError("No first frame image available for acting video")

                prompt = block.acting_prompt or "The person performs a natural gesture."

                client = ActingVideoClient(settings.FAL_API_KEY)
                result = await client.generate(
                    first_frame_url=first_frame_url,
                    last_frame_url=last_frame_url,
                    prompt=prompt,
                    duration_seconds=5,
                    aspect_ratio="9:16",
                )

                # Download and upload to R2
                async with httpx.AsyncClient() as http:
                    video_resp = await http.get(result["video_url"], timeout=120)
                    video_resp.raise_for_status()
                    video_bytes = video_resp.content

                r2_key = f"casts/{cast.id}/acting/{block_id}/acting_video.mp4"
                await r2.upload_bytes(video_bytes, r2_key, "video/mp4")

                # Update block
                block.acting_video_r2_key = r2_key
                block.acting_video_duration_seconds = result["duration_seconds"]

                # Log cost
                cost_entry = GenerationCost(
                    id=f"gc_{uuid.uuid4().hex[:16]}",
                    user_id=user_id,
                    avatar_id=avatar.id,
                    cast_id=cast.id,
                    block_id=block_id,
                    engine=result["engine"],
                    operation="acting_video",
                    cost_usd=result["cost_usd"],
                    quantity=1,
                    metadata_json={
                        "duration_seconds": result["duration_seconds"],
                        "first_frame_angle": block.acting_first_frame_angle,
                        "last_frame_angle": block.acting_last_frame_angle,
                    },
                )
                session.add(cost_entry)

                # Update render job
                rj.state = RenderJobState.COMPLETED
                await session.commit()

                logger.info(
                    "Acting video generated for block %s: r2_key=%s, cost=$%.4f",
                    block_id, r2_key, result["cost_usd"],
                )

            except Exception as e:
                sentry_sdk.capture_exception(e)
                rj.state = RenderJobState.FAILED
                rj.error_message = str(e)[:500]
                await session.commit()
                logger.error("Acting video generation failed for block %s: %s", block_id, e)
                raise

    asyncio.run(_run())


async def _get_body_shot_url(session, avatar, angle: str, r2) -> str | None:
    """Get the body shot URL for a given avatar and angle."""
    from sqlalchemy import select
    from models.avatar import BodyShotSet

    result = await session.execute(
        select(BodyShotSet)
        .where(BodyShotSet.avatar_id == avatar.id)
        .order_by(BodyShotSet.created_at.desc())
        .limit(1)
    )
    bss = result.scalar_one_or_none()
    if not bss or not bss.angles:
        return None

    r2_key = bss.angles.get(angle)
    if not r2_key:
        return None

    return r2.get_public_url(r2_key)
