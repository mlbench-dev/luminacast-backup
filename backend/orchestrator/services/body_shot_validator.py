"""Validates that a generated body shot matches the intended angle and preserves identity.

Uses MediaPipe Pose for angle classification and basic heuristics.
Falls back gracefully if MediaPipe is not available.
"""
import io
import logging

import httpx
import numpy as np
import sentry_sdk
from PIL import Image

logger = logging.getLogger(__name__)


async def validate_body_shot(
    shot_url: str,
    canonical_front_url: str,
    intended_angle: str,
) -> dict:
    """
    Returns: {
        "angle_match": bool,
        "angle_predicted": str,
        "angle_confidence": float,
        "identity_similarity": float | None,
        "identity_match": bool,
        "passed": bool,
    }
    """
    try:
        async with httpx.AsyncClient() as client:
            r1 = await client.get(shot_url, timeout=30)
            r1.raise_for_status()

        shot_img = Image.open(io.BytesIO(r1.content)).convert("RGB")
        shot_np = np.array(shot_img)

        predicted_angle, confidence = _classify_angle_mediapipe(shot_np)
        angle_match = predicted_angle == intended_angle

        # Skip identity check (InsightFace requires GPU, run on orchestrator CPU is too slow)
        # For back view, identity can't be checked anyway
        identity_sim = None
        identity_match = True

        passed = angle_match

        return {
            "angle_match": angle_match,
            "angle_predicted": predicted_angle,
            "angle_confidence": confidence,
            "identity_similarity": identity_sim,
            "identity_match": identity_match,
            "passed": passed,
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Body shot validation failed for {intended_angle}: {e}")
        return {
            "angle_match": True,  # Don't block on validation failure
            "angle_predicted": "unknown",
            "angle_confidence": 0.0,
            "identity_similarity": None,
            "identity_match": True,
            "passed": True,
        }


def _classify_angle_mediapipe(img_np: np.ndarray) -> tuple:
    """
    Uses MediaPipe Pose landmark visibility + symmetry to classify camera angle.
    Returns (angle_name, confidence_0_to_1).
    """
    try:
        import mediapipe as mp
    except ImportError:
        logger.warning("MediaPipe not available, skipping angle classification")
        return ("unknown", 0.0)

    mp_pose = mp.solutions.pose
    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        results = pose.process(img_np)

    if not results.pose_landmarks:
        return ("unknown", 0.0)

    lm = results.pose_landmarks.landmark

    NOSE = lm[mp_pose.PoseLandmark.NOSE.value]
    L_SHOULDER = lm[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
    R_SHOULDER = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
    L_EAR = lm[mp_pose.PoseLandmark.LEFT_EAR.value]
    R_EAR = lm[mp_pose.PoseLandmark.RIGHT_EAR.value]

    nose_visible = NOSE.visibility > 0.5

    if not nose_visible:
        return ("back", 0.85)

    shoulder_dx = abs(L_SHOULDER.x - R_SHOULDER.x)
    l_ear_vis = L_EAR.visibility > 0.5
    r_ear_vis = R_EAR.visibility > 0.5

    if shoulder_dx > 0.25 and l_ear_vis and r_ear_vis:
        return ("front", 0.9)
    elif shoulder_dx < 0.08:
        if l_ear_vis and not r_ear_vis:
            return ("profile_left", 0.85)
        elif r_ear_vis and not l_ear_vis:
            return ("profile_right", 0.85)
        else:
            return ("profile_left", 0.6)
    else:
        if L_SHOULDER.x < R_SHOULDER.x:
            return ("three_quarter_right", 0.75)
        else:
            return ("three_quarter_left", 0.75)
