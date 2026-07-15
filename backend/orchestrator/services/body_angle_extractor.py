"""Body angle extraction from video using MediaPipe Pose (Tasks API)."""
import logging
import os
from dataclasses import dataclass
from enum import Enum

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    PoseLandmarker,
    PoseLandmarkerOptions,
    PoseLandmark,
    RunningMode,
)

logger = logging.getLogger(__name__)

POSE_MODEL_PATH = os.environ.get(
    "POSE_MODEL_PATH",
    "/app/models/pose_landmarker_full.task",
)


class PoseAngle(str, Enum):
    FRONT = "front"
    THREE_QUARTER_LEFT = "three_quarter_left"
    THREE_QUARTER_RIGHT = "three_quarter_right"
    PROFILE_LEFT = "profile_left"
    PROFILE_RIGHT = "profile_right"
    BACK = "back"


@dataclass
class AngleFrame:
    angle: PoseAngle
    frame_index: int
    image: np.ndarray  # BGR numpy array
    confidence: float  # pose detection confidence
    shoulder_angle_deg: float  # raw angle for debugging


def classify_shoulder_angle(
    left_shoulder, right_shoulder,
) -> tuple[PoseAngle | None, float]:
    """
    Classify body orientation from shoulder landmark positions.

    Uses x-distance (shoulder width in image space) and z-depth difference
    to determine facing direction.
    """
    dx = left_shoulder.x - right_shoulder.x
    dz = left_shoulder.z - right_shoulder.z  # positive = left further from camera

    shoulder_width = abs(dx)
    z_ratio = dz

    if shoulder_width < 0.05:
        if abs(dz) > 0.1:
            return (PoseAngle.PROFILE_LEFT if dz > 0 else PoseAngle.PROFILE_RIGHT, shoulder_width * 100)
        return (PoseAngle.BACK, shoulder_width * 100)

    if shoulder_width > 0.25:
        if abs(z_ratio) < 0.05:
            return (PoseAngle.FRONT, shoulder_width * 100)
        elif z_ratio > 0.05:
            return (PoseAngle.THREE_QUARTER_RIGHT, shoulder_width * 100)
        else:
            return (PoseAngle.THREE_QUARTER_LEFT, shoulder_width * 100)

    if abs(z_ratio) < 0.03:
        return (PoseAngle.FRONT, shoulder_width * 100)
    elif z_ratio > 0.08:
        return (
            (PoseAngle.PROFILE_RIGHT if shoulder_width < 0.12 else PoseAngle.THREE_QUARTER_RIGHT),
            shoulder_width * 100,
        )
    elif z_ratio < -0.08:
        return (
            (PoseAngle.PROFILE_LEFT if shoulder_width < 0.12 else PoseAngle.THREE_QUARTER_LEFT),
            shoulder_width * 100,
        )
    else:
        return (
            PoseAngle.THREE_QUARTER_RIGHT if z_ratio > 0 else PoseAngle.THREE_QUARTER_LEFT,
            shoulder_width * 100,
        )


def extract_body_angles(
    video_path: str,
    every_nth_frame: int = 10,
    min_confidence: float = 0.5,
) -> dict[PoseAngle, AngleFrame]:
    """
    Extract the best frame per body angle from a video.

    Returns a dict mapping PoseAngle -> AngleFrame for each angle found.
    Not all angles may be present (creator might never turn around).
    """
    if not os.path.exists(POSE_MODEL_PATH):
        logger.error("Pose landmarker model not found at %s", POSE_MODEL_PATH)
        return {}

    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=POSE_MODEL_PATH),
        running_mode=RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=min_confidence,
        min_tracking_confidence=min_confidence,
    )

    landmarker = PoseLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("Cannot open video: %s", video_path)
        landmarker.close()
        return {}

    best_per_angle: dict[PoseAngle, AngleFrame] = {}
    frame_idx = 0
    processed = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % every_nth_frame != 0:
                frame_idx += 1
                continue

            # Convert to MediaPipe Image
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            # Run pose detection
            result = landmarker.detect(mp_image)

            if not result.pose_landmarks or len(result.pose_landmarks) == 0:
                frame_idx += 1
                continue

            landmarks = result.pose_landmarks[0]
            left_shoulder = landmarks[PoseLandmark.LEFT_SHOULDER]
            right_shoulder = landmarks[PoseLandmark.RIGHT_SHOULDER]

            # Both shoulders must be visible
            if left_shoulder.visibility < min_confidence or right_shoulder.visibility < min_confidence:
                frame_idx += 1
                continue

            angle, raw_deg = classify_shoulder_angle(left_shoulder, right_shoulder)
            if angle is None:
                frame_idx += 1
                continue

            score = (left_shoulder.visibility + right_shoulder.visibility) / 2

            candidate = AngleFrame(
                angle=angle,
                frame_index=frame_idx,
                image=frame.copy(),
                confidence=score,
                shoulder_angle_deg=raw_deg,
            )

            if angle not in best_per_angle or score > best_per_angle[angle].confidence:
                best_per_angle[angle] = candidate

            processed += 1
            frame_idx += 1
    finally:
        cap.release()
        landmarker.close()

    logger.info(
        "Body angle extraction: %d frames scanned, %d with pose, %d angles found: %s",
        frame_idx, processed, len(best_per_angle),
        [a.value for a in best_per_angle.keys()],
    )
    return best_per_angle


def crop_body_portrait(
    frame: np.ndarray, target_width: int = 768, target_height: int = 1024,
) -> np.ndarray:
    """Crop a frame to a body portrait (center-weighted, aspect-preserved)."""
    h, w = frame.shape[:2]
    target_aspect = target_width / target_height
    frame_aspect = w / h

    if frame_aspect > target_aspect:
        new_w = int(h * target_aspect)
        x_start = (w - new_w) // 2
        cropped = frame[:, x_start:x_start + new_w]
    else:
        new_h = int(w / target_aspect)
        y_start = max(0, (h - new_h) // 4)  # bias upward
        cropped = frame[y_start:y_start + new_h, :]

    return cv2.resize(cropped, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)
