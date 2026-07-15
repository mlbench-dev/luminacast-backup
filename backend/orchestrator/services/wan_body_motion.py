"""Wan 2.7 image-to-video body motion generation via fal.ai."""
import asyncio
import logging
import os

import sentry_sdk

logger = logging.getLogger(__name__)

# Canonical fal.ai endpoint for the Wan 2.7 image-to-video body-motion path
# (avatar_action / motion-with-reference-image). This is the I2V endpoint —
# distinct from the Wan T2V endpoint in ``wan_tti2v_client`` and the Kling
# Elements I2V endpoint in ``render_providers``.
FAL_WAN_27_I2V_ENDPOINT = "fal-ai/wan/v2.7/image-to-video"


def _endpoint_is_wan_i2v(model_id: str) -> bool:
    """True for the Wan 2.7 image-to-video endpoint (the duration-as-int path)."""
    return "image-to-video" in model_id


# fal Wan 2.7 I2V validates ``duration`` as an integer enum (2..15); PR #103's
# blanket ``str(int(...))`` made it 422 on live retest of cst_d2dd91985028
# ("Input should be 2, 3, ... 15", input='12'). When this is true (default) the
# I2V branch sends an int. Flip to false to force the old string behaviour
# without a code change if fal changes their API
# (Render_Quality_Duration_Validation.md §1.1).
WAN_I2V_DURATION_AS_INT = (
    os.environ.get("WAN_I2V_DURATION_AS_INT", "true").strip().lower()
    not in ("0", "false", "no", "off")
)


def _wan_duration_param(duration_int: int, model_id: str) -> int | str:
    """Per-endpoint duration param for a fal Wan call.

    fal Wan 2.7 I2V wants an integer enum member (2..15); fal Wan T2V and Kling
    want a string. Verified via live retest: I2V with ``duration="12"`` → 422
    literal_error expecting int (Render_Quality_Duration_Validation.md §1.1).
    """
    if _endpoint_is_wan_i2v(model_id) and WAN_I2V_DURATION_AS_INT:
        return int(duration_int)
    return str(int(duration_int))


def _coerce_returned_duration(result: object, fallback_s: float) -> float:
    """Best-effort pull of the model's reported clip duration from a result."""
    if isinstance(result, dict):
        video = result.get("video")
        if isinstance(video, dict) and "duration" in video:
            try:
                return float(video["duration"])
            except (TypeError, ValueError) as exc:
                sentry_sdk.capture_exception(exc)
    return float(fallback_s)


def _extract_video_url(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    video = result.get("video")
    if isinstance(video, dict):
        return video.get("url")
    if isinstance(video, str):
        return video
    return None


async def _submit_wan_segment(
    *,
    start_image_url: str,
    end_image_url: str | None,
    full_prompt: str,
    duration_int: int,
    resolution: str,
) -> dict:
    """Submit a single Wan 2.7 I2V segment.

    ``duration`` is sent per-endpoint: the fal.ai Wan 2.7 *image-to-video*
    endpoint validates ``duration`` as an INTEGER enum member (2..15), while
    the Wan *text-to-video* endpoint (and Kling) want a string. PR #103's
    blanket ``str(int(...))`` over-corrected and made I2V 422 on live retest of
    cst_d2dd91985028 ("Input should be 2, 3, ... 15", input='12'). The param
    type is now resolved by ``_wan_duration_param`` against the endpoint id and
    the ``WAN_I2V_DURATION_AS_INT`` override
    (Render_Quality_Duration_Validation.md §1.1).
    """
    import fal_client

    duration_param = _wan_duration_param(duration_int, FAL_WAN_27_I2V_ENDPOINT)

    def call_wan():
        arguments: dict = {
            "image_url": start_image_url,
            "prompt": full_prompt,
            "negative_prompt": "blurry, distorted, deformed face, bad anatomy, static, frozen, glitch",
            "duration": duration_param,
            "resolution": resolution,
            "enable_safety_checker": False,
        }
        if end_image_url:
            arguments["end_image_url"] = end_image_url
        return fal_client.subscribe(
            FAL_WAN_27_I2V_ENDPOINT,
            arguments=arguments,
        )

    logger.info(
        "motion bake request provider=wan_i2v param_type=%s param_value=%r "
        "tier=%ds resolution=%s",
        type(duration_param).__name__, duration_param, duration_int, resolution,
    )
    result = await asyncio.to_thread(call_wan)

    video_url = _extract_video_url(result)
    if not video_url:
        raise RuntimeError(f"Wan 2.7 returned no video: {str(result)[:300]}")

    returned_s = _coerce_returned_duration(result, float(duration_int))
    delta = returned_s - float(duration_int)
    pct = (delta / float(duration_int) * 100.0) if duration_int else 0.0
    logger.info(
        "motion bake result provider=wan_i2v requested=%ds returned=%.3fs "
        "delta=%+.3fs (%+.1f%%) url=%s",
        duration_int, returned_s, delta, pct, video_url[:100],
    )
    return {"video_url": video_url, "duration_seconds": returned_s}


async def generate_body_motion_clip(
    start_image_url: str,
    end_image_url: str | None,
    prompt: str,
    duration_seconds: float = 5.0,
    resolution: str = "720p",
) -> dict:
    """
    Generate a body motion video clip using Wan 2.7 image-to-video on fal.ai.

    Args:
        start_image_url: Public URL of the start frame (body motion photo)
        end_image_url: Public URL of the end frame, or None to let the model
            extrapolate freely from the start frame + prompt. Used when the
            block has no AI-generated last frame yet (legacy avatar_acting
            data, or mid-migration avatar_action blocks). When chaining
            multiple segments the end frame only conditions the FINAL segment;
            interior segments are open-ended so the motion flows continuously.
        prompt: Motion description (e.g. "walks confidently across the frame")
        duration_seconds: The block's SLOT length in seconds. This helper
            applies the shared motion overshoot (slot × MOTION_OVERSHOOT_FACTOR)
            and snaps up to the Wan 2.7 I2V integer tier set so the bake is
            always ≥ slot; the surplus is head-trimmed downstream. Slots longer
            than the max tier are chained across multiple segments, with the
            overshoot applied only to the final segment
            (Render_Quality_Duration_Validation.md §1.1, §1.1.3). Callers MUST
            pass the raw slot float — do NOT pre-round or pre-overshoot.
        resolution: "720p" or "1080p"

    Returns:
        {"video_url": str, "duration_seconds": float}
    """
    from config import settings
    from services.render_providers import (
        MOTION_OVERSHOOT_FACTOR,
        WAN_27_I2V_DURATIONS,
        WAN_27_I2V_MAX_S,
        _overshoot_target_s,
        _plan_wan_i2v_segments,
        _snap_up_tier,
    )

    if not os.environ.get("FAL_KEY") and settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = settings.FAL_API_KEY

    full_prompt = (
        f"A person {prompt}. "
        f"Smooth natural motion, professional lighting, photorealistic, "
        f"high quality video, consistent identity throughout."
    )

    slot_s = float(duration_seconds) if duration_seconds else 5.0
    segments = _plan_wan_i2v_segments(slot_s)
    if not segments:
        segments = [_snap_up_tier(_overshoot_target_s(slot_s), WAN_27_I2V_DURATIONS)]

    # Overshoot applies ONLY to the final segment's tier selection so the
    # chained total is ≥ slot while interior joins stay frame-exact.
    final_overshot = _snap_up_tier(
        _overshoot_target_s(float(segments[-1])), WAN_27_I2V_DURATIONS
    )
    segments[-1] = final_overshot
    target_total = float(sum(segments))

    logger.info(
        "motion bake request provider=wan_i2v block_slot=%.3fs overshoot=%.2f "
        "target=%.3fs tier_set=%s segments=%s",
        slot_s, MOTION_OVERSHOOT_FACTOR, target_total,
        f"{WAN_27_I2V_DURATIONS[0]}..{WAN_27_I2V_DURATIONS[-1]}", segments,
    )

    if len(segments) == 1:
        return await _submit_wan_segment(
            start_image_url=start_image_url,
            end_image_url=end_image_url,
            full_prompt=full_prompt,
            duration_int=segments[0],
            resolution=resolution,
        )

    # Multi-segment chaining: each segment starts from the same conditioning
    # start frame; only the FINAL segment carries the block's end frame. The
    # segment URLs are ffmpeg-concatenated (stream-copy demuxer) and the joined
    # mp4 is uploaded to R2, so downstream single-URL consumers see one clip.
    from services.render_providers import _stitch_segment_urls

    seg_urls: list[str] = []
    total_returned = 0.0
    for idx, seg_int in enumerate(segments):
        is_final = idx == len(segments) - 1
        seg_result = await _submit_wan_segment(
            start_image_url=start_image_url,
            end_image_url=end_image_url if is_final else None,
            full_prompt=full_prompt,
            duration_int=seg_int,
            resolution=resolution,
        )
        seg_urls.append(seg_result["video_url"])
        total_returned += float(seg_result.get("duration_seconds") or seg_int)

    stitched_url = await _stitch_segment_urls(seg_urls, total_duration_s=target_total)
    logger.info(
        "motion bake result provider=wan_i2v chained segments=%d "
        "total_returned=%.3fs target=%.3fs url=%s",
        len(seg_urls), total_returned, target_total, str(stitched_url)[:100],
    )
    return {"video_url": stitched_url, "duration_seconds": total_returned}
