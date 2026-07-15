"""Render planning — pick the right InfiniteTalk resolution for a block.

From RunPodService.SIZE_MAP (discovered Session B Phase 2):
  480p: (480, 854)
  720p: (720, 1280)
  1080p: (1080, 1920)
"""
from typing import Optional

# Ordered by total pixel count ascending.
INFINITETALK_SUPPORTED_SIZES = [
    ("480p", 480, 854),    # 410,160 px
    ("720p", 720, 1280),   # 921,600 px
    ("1080p", 1080, 1920), # 2,073,600 px
]

# Canvas dimensions by output format
CANVAS_DIMENSIONS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

# InfiniteTalk sizes by orientation
INFINITETALK_PORTRAIT_SIZES = [
    ("480p", 480, 854),
    ("720p", 720, 1280),
    ("1080p", 1080, 1920),
]

INFINITETALK_LANDSCAPE_SIZES = [
    ("480p", 854, 480),
    ("720p", 1280, 720),
    ("1080p", 1920, 1080),
]

INFINITETALK_SQUARE_SIZES = [
    ("480p", 480, 480),
    ("720p", 720, 720),
    ("1080p", 1080, 1080),
]


def get_canvas_dimensions(output_format: str) -> tuple:
    """Returns (width, height) for the given output format."""
    return CANVAS_DIMENSIONS.get(output_format, (1080, 1920))


def get_infinitetalk_sizes(output_format: str):
    """Returns the InfiniteTalk size table for the given output format."""
    if output_format in ("16:9",):
        return INFINITETALK_LANDSCAPE_SIZES
    elif output_format in ("1:1",):
        return INFINITETALK_SQUARE_SIZES
    else:
        return INFINITETALK_PORTRAIT_SIZES


def pick_render_size_for_pip(pip_width_px: int, pip_height_px: int, output_format: str = "9:16") -> tuple:
    """Given the PIP actual rendered dimensions, return the smallest
    supported InfiniteTalk preset whose dimensions cover the PIP.
    Returns (size_str, width, height)."""
    size_table = get_infinitetalk_sizes(output_format)
    for preset, w, h in size_table:
        if w >= pip_width_px and h >= pip_height_px:
            return (preset, w, h)
    # Fallback: largest
    return size_table[-1]


def get_full_render_size(output_format: str = "9:16") -> tuple:
    """Return the default full-target render size for the given output format."""
    size_table = get_infinitetalk_sizes(output_format)
    # Default: second size (720p equivalent)
    if len(size_table) >= 2:
        return size_table[1]
    return size_table[-1]
