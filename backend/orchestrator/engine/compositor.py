"""
Scene Compositor — FFmpeg filter graph builder for 6 layout modes.
Uses ffmpeg-python to construct filter graphs.
"""
from models.block import LayoutMode
import logging

logger = logging.getLogger(__name__)

# Output specs
OUTPUT_WIDTH = 720
OUTPUT_HEIGHT = 1280
OUTPUT_FPS = 30
VIDEO_BITRATE = "2500k"
AUDIO_BITRATE = "128k"
AUDIO_SAMPLE_RATE = 44100


def build_filter_graph(layout: LayoutMode, clip_path: str, overlay_path: str | None = None,
                       text_overlay: str | None = None, text_file_path: str | None = None,
                       avatar_fit_mode: str = "cover") -> list[str]:
    """
    Build FFmpeg command arguments for the given layout mode.
    Returns list of FFmpeg arguments (not including the ffmpeg binary itself).

    Args:
        layout: The layout mode enum
        clip_path: Path to the avatar video clip
        overlay_path: Path to the product overlay image (if applicable)
        text_overlay: Text to display (price, CTA, etc.)
        text_file_path: Path to text file for drawtext reload
        avatar_fit_mode: "cover" (crop to fill), "contain" (fit with padding)
    """
    base_args = ["-i", clip_path]
    filter_parts = []

    def _avatar_scale_expr(width: int, height: int, mode: str) -> str:
        """Build the avatar scale+pad/crop expression."""
        if mode == "contain":
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

    if layout == LayoutMode.FULL_AVATAR:
        filter_parts.append(_avatar_scale_expr(OUTPUT_WIDTH, OUTPUT_HEIGHT, avatar_fit_mode))

    elif layout == LayoutMode.AVATAR_PRODUCT:
        avatar_expr = _avatar_scale_expr(OUTPUT_WIDTH, OUTPUT_HEIGHT, avatar_fit_mode)
        if overlay_path:
            base_args.extend(["-i", overlay_path])
            filter_parts.append(
                f"[0:v]{avatar_expr}[base];"
                f"[1:v]scale=200:-1[ovl];"
                f"[base][ovl]overlay=10:H-h-10"
            )
        else:
            filter_parts.append(avatar_expr)

    elif layout == LayoutMode.SPLIT_SCREEN:
        half_w = OUTPUT_WIDTH // 2
        avatar_expr = _avatar_scale_expr(half_w, OUTPUT_HEIGHT, avatar_fit_mode)
        if overlay_path:
            base_args.extend(["-i", overlay_path])
            filter_parts.append(
                f"[0:v]{avatar_expr}[left];"
                f"[1:v]scale={half_w}:{OUTPUT_HEIGHT}[right];"
                f"[left][right]hstack"
            )
        else:
            filter_parts.append(_avatar_scale_expr(OUTPUT_WIDTH, OUTPUT_HEIGHT, avatar_fit_mode))

    elif layout == LayoutMode.PIP_PRODUCT:
        avatar_pip_w = OUTPUT_WIDTH // 4
        avatar_pip_h = avatar_pip_w * 16 // 9
        avatar_expr = _avatar_scale_expr(avatar_pip_w, avatar_pip_h, avatar_fit_mode)
        if overlay_path:
            base_args.extend(["-i", overlay_path])
            filter_parts.append(
                f"[1:v]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}[bg];"
                f"[0:v]{avatar_expr}[pip];"
                f"[bg][pip]overlay=W-w-10:H-h-10"
            )
        else:
            filter_parts.append(_avatar_scale_expr(OUTPUT_WIDTH, OUTPUT_HEIGHT, avatar_fit_mode))

    elif layout == LayoutMode.PRODUCT_ONLY:
        if overlay_path:
            base_args = ["-i", overlay_path]
        filter_parts.append(f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}")

    elif layout == LayoutMode.AUTO_BASKET:
        filter_parts.append(_avatar_scale_expr(OUTPUT_WIDTH, OUTPUT_HEIGHT, avatar_fit_mode))
        if text_file_path:
            filter_parts[-1] += (
                f",drawtext=textfile='{text_file_path}'"
                f":reload=1:fontsize=36:fontcolor=white"
                f":x=(w-text_w)/2:y=h-80"
                f":box=1:boxcolor=red@0.7:boxborderw=10"
            )

    # Add text overlay if provided
    if text_overlay and layout != LayoutMode.AUTO_BASKET:
        if filter_parts:
            filter_parts[-1] += (
                f",drawtext=text='{text_overlay}'"
                f":fontsize=28:fontcolor=white"
                f":x=(w-text_w)/2:y=h-60"
                f":box=1:boxcolor=black@0.5:boxborderw=5"
            )

    # Build full command args
    cmd_args = base_args[:]
    if filter_parts:
        complex_filter = filter_parts[0] if len(filter_parts) == 1 else ";".join(filter_parts)
        if "[" in complex_filter:
            cmd_args.extend(["-filter_complex", complex_filter])
        else:
            cmd_args.extend(["-vf", complex_filter])

    return cmd_args


def build_stream_output_args(rtmp_url: str) -> list[str]:
    """Build FFmpeg output arguments for RTMP streaming."""
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-profile:v", "baseline",
        "-b:v", VIDEO_BITRATE,
        "-maxrate", VIDEO_BITRATE,
        "-bufsize", str(int(VIDEO_BITRATE.replace("k", "")) * 2) + "k",
        "-r", str(OUTPUT_FPS),
        "-g", str(OUTPUT_FPS * 2),  # Keyframe every 2 seconds
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", "2",
        "-f", "flv",
        "-flvflags", "no_duration_filesize",
        rtmp_url,
    ]


def build_idle_loop_args(idle_clip_path: str, rtmp_url: str) -> list[str]:
    """Build FFmpeg args for idle loop (loops a single clip)."""
    return [
        "-stream_loop", "-1",
        "-re",
        "-i", idle_clip_path,
        *build_stream_output_args(rtmp_url),
    ]
