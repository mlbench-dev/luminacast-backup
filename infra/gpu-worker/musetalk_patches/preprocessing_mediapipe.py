"""Preprocessing module — patched to use MediaPipe FaceLandmarker (Tasks API) instead of mmpose.

Original used mmpose dwpose (wholebody keypoints 23-91 = face landmarks).
This version uses MediaPipe FaceLandmarker (478 landmarks) mapped to equivalent positions.
"""
import sys
import numpy as np
import cv2
import pickle
import os
import json
from tqdm import tqdm
import torch

import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions

# Initialize MediaPipe FaceLandmarker (replaces mmpose dwpose)
_face_landmarker = None
_LANDMARKER_MODEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "models", "face_landmarker_v2_with_blendshapes.task"
)


def _get_face_landmarker():
    global _face_landmarker
    if _face_landmarker is None:
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=_LANDMARKER_MODEL),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        _face_landmarker = vision.FaceLandmarker.create_from_options(options)
    return _face_landmarker


def _mediapipe_get_face_landmarks(image):
    """Get face landmarks using MediaPipe FaceLandmarker (Tasks API).

    Returns a numpy array of 68 face landmarks (x, y) mapped to approximate
    mmpose wholebody face keypoint positions (indices 23-91).

    The key landmarks used by MuseTalk preprocessing are:
      - landmark[28] (nose bridge upper)
      - landmark[29] (nose bridge mid)
      - landmark[30] (nose tip)
    These control the bbox vertical split point.
    """
    landmarker = _get_face_landmarker()
    h, w = image.shape[:2]

    # MediaPipe Tasks API needs RGB input as mp.Image
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    result = landmarker.detect(mp_image)

    if not result.face_landmarks or len(result.face_landmarks) == 0:
        return None

    face_lm = result.face_landmarks[0]

    # Build a 68-point face landmark array mapping MediaPipe to dlib/mmpose order.
    all_indices = []

    # Landmarks 0-16: jaw line (17 points)
    jaw_indices = [234, 93, 132, 58, 172, 136, 150, 149, 148, 152,
                   377, 400, 378, 379, 365, 397, 288]
    all_indices.extend(jaw_indices)

    # Landmarks 17-21: left eyebrow (5 points)
    all_indices.extend([70, 63, 105, 66, 107])

    # Landmarks 22-26: right eyebrow (5 points)
    all_indices.extend([336, 296, 334, 293, 300])

    # Landmarks 27-30: nose bridge (4 points) -- CRITICAL for MuseTalk
    all_indices.extend([
        6,    # 27: nose bridge top (between eyes)
        6,    # 28: nose bridge upper
        168,  # 29: nose bridge mid  -- key landmark for bbox split
        1,    # 30: nose tip         -- key landmark for bbox shift
    ])

    # Landmarks 31-35: nose bottom (5 points)
    all_indices.extend([49, 220, 4, 440, 279])

    # Landmarks 36-41: left eye (6 points)
    all_indices.extend([33, 160, 158, 133, 153, 144])

    # Landmarks 42-47: right eye (6 points)
    all_indices.extend([362, 385, 387, 263, 373, 380])

    # Landmarks 48-59: outer lip (12 points)
    all_indices.extend([61, 40, 37, 0, 267, 270, 291, 321, 314, 17, 84, 91])

    # Landmarks 60-67: inner lip (8 points)
    all_indices.extend([78, 82, 13, 312, 308, 317, 14, 87])

    # Convert to pixel coordinates
    landmarks = np.zeros((68, 2), dtype=np.int32)
    for i, mp_idx in enumerate(all_indices):
        lm = face_lm[mp_idx]
        landmarks[i] = [int(lm.x * w), int(lm.y * h)]

    return landmarks


# Initialize face detection (SFD-based, no mmpose dependency)
from musetalk.utils.face_detection import FaceAlignment, LandmarksType
device = "cuda" if torch.cuda.is_available() else "cpu"
fa = FaceAlignment(LandmarksType._2D, flip_input=False, device=device)

# Marker if the bbox is not sufficient
coord_placeholder = (0.0, 0.0, 0.0, 0.0)


def resize_landmark(landmark, w, h, new_w, new_h):
    w_ratio = new_w / w
    h_ratio = new_h / h
    landmark_norm = landmark / [w, h]
    landmark_resized = landmark_norm * [new_w, new_h]
    return landmark_resized


def read_imgs(img_list):
    frames = []
    print("reading images...")
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames


def get_landmark_and_bbox(img_list, upperbondrange=0):
    """Get face landmarks and bounding boxes using MediaPipe + SFD.

    Replaces the original mmpose-based implementation.
    """
    frames = read_imgs(img_list)
    batch_size_fa = 1
    batches = [frames[i:i + batch_size_fa] for i in range(0, len(frames), batch_size_fa)]
    coords_list = []

    if upperbondrange != 0:
        print("get key_landmark and face bounding boxes with the bbox_shift:", upperbondrange)
    else:
        print("get key_landmark and face bounding boxes with the default value")

    average_range_minus = []
    average_range_plus = []

    for fb in tqdm(batches):
        img = np.asarray(fb)[0]

        # Use MediaPipe for face landmarks (replaces mmpose inference_topdown)
        face_land_mark = _mediapipe_get_face_landmarks(img)

        if face_land_mark is None:
            coords_list += [coord_placeholder]
            continue

        # Get bounding boxes by SFD face detection (unchanged)
        bbox = fa.get_detections_for_batch(np.asarray(fb))

        for j, f in enumerate(bbox):
            if f is None:
                coords_list += [coord_placeholder]
                continue

            half_face_coord = face_land_mark[29].copy()
            range_minus = (face_land_mark[30] - face_land_mark[29])[1]
            range_plus = (face_land_mark[29] - face_land_mark[28])[1]
            average_range_minus.append(range_minus)
            average_range_plus.append(range_plus)

            if upperbondrange != 0:
                half_face_coord[1] = upperbondrange + half_face_coord[1]

            half_face_dist = np.max(face_land_mark[:, 1]) - half_face_coord[1]
            min_upper_bond = 0
            upper_bond = max(min_upper_bond, half_face_coord[1] - half_face_dist)

            f_landmark = (
                np.min(face_land_mark[:, 0]),
                int(upper_bond),
                np.max(face_land_mark[:, 0]),
                np.max(face_land_mark[:, 1]),
            )
            x1, y1, x2, y2 = f_landmark

            if y2 - y1 <= 0 or x2 - x1 <= 0 or x1 < 0:
                coords_list += [f]
                print("error bbox:", f)
            else:
                coords_list += [f_landmark]

    if average_range_minus and average_range_plus:
        print("*" * 60 + "bbox_shift parameter adjustment" + "*" * 60)
        print(
            "Total frame: [%d] Manually adjust range: "
            "[ -%d~%d ] , the current value: %d"
            % (
                len(frames),
                int(sum(average_range_minus) / len(average_range_minus)),
                int(sum(average_range_plus) / len(average_range_plus)),
                upperbondrange,
            )
        )
        print("*" * 120)

    return coords_list, frames


def get_bbox_range(img_list, upperbondrange=0):
    """Get bbox range info using MediaPipe (replaces mmpose version)."""
    frames = read_imgs(img_list)
    batch_size_fa = 1
    batches = [frames[i:i + batch_size_fa] for i in range(0, len(frames), batch_size_fa)]

    if upperbondrange != 0:
        print("get key_landmark and face bounding boxes with the bbox_shift:", upperbondrange)
    else:
        print("get key_landmark and face bounding boxes with the default value")

    average_range_minus = []
    average_range_plus = []

    for fb in tqdm(batches):
        img = np.asarray(fb)[0]
        face_land_mark = _mediapipe_get_face_landmarks(img)

        if face_land_mark is None:
            continue

        bbox = fa.get_detections_for_batch(np.asarray(fb))

        for j, f in enumerate(bbox):
            if f is None:
                continue

            half_face_coord = face_land_mark[29].copy()
            range_minus = (face_land_mark[30] - face_land_mark[29])[1]
            range_plus = (face_land_mark[29] - face_land_mark[28])[1]
            average_range_minus.append(range_minus)
            average_range_plus.append(range_plus)

            if upperbondrange != 0:
                half_face_coord[1] = upperbondrange + half_face_coord[1]

    if average_range_minus and average_range_plus:
        text_range = (
            "Total frame: [%d] Manually adjust range: "
            "[ -%d~%d ] , the current value: %d"
            % (
                len(frames),
                int(sum(average_range_minus) / len(average_range_minus)),
                int(sum(average_range_plus) / len(average_range_plus)),
                upperbondrange,
            )
        )
    else:
        text_range = "Total frame: [%d] No faces detected" % len(frames)

    return text_range


if __name__ == "__main__":
    img_list = [
        "./results/lyria/00000.png",
        "./results/lyria/00001.png",
        "./results/lyria/00002.png",
        "./results/lyria/00003.png",
    ]
    crop_coord_path = "./coord_face.pkl"
    coords_list, full_frames = get_landmark_and_bbox(img_list)
    with open(crop_coord_path, "wb") as f:
        pickle.dump(coords_list, f)

    for bbox, frame in zip(coords_list, full_frames):
        if bbox == coord_placeholder:
            continue
        x1, y1, x2, y2 = bbox
        crop_frame = frame[y1:y2, x1:x2]
        print("Cropped shape", crop_frame.shape)
    print(coords_list)
