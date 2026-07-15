"""RTMP compositor for Go-Live sessions.

The compositor takes the session's cast renders (one per product rotation
slot) and produces a continuous RTMP feed pushed to the local SRS relay.
The user's machine (or OBS browser source) pulls from that relay and
forwards to TikTok / Instagram / YouTube / etc.

# MVP scope (Phase 1)
- Plays the selected cast renders sequentially on loop.
- Burns a small product-info overlay (top-left).
- Single ffmpeg process per session.
- No traction-driven decisions yet; that lives in LiveOrchestrator.

# Out of scope (Phase 2)
- Reactive voiceover injection (Fish Speech \u2192 audio mux)
- Background music ducking
- Chat overlay
- Per-platform aspect-ratio variants

# Architecture
The session "starts" by spawning a Celery long-running task. The task
builds an ffmpeg concat playlist from the selected renders' R2 URLs and
streams to rtmp://<relay>/live/<relay_stream_key>. The task writes
heartbeat + bitrate to LiveSession every ~5s.

Stopping the session is cooperative \u2014 the API endpoint sets status='ended'
and the task notices on its next heartbeat tick.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_concat_playlist(render_urls: list[str], loop: bool = True) -> str:
    """Build an ffmpeg concat-demuxer playlist from a list of render URLs.

    Returns the contents of a `.ffconcat` file. Caller writes it to /tmp
    and feeds it to ffmpeg via `-i playlist.ffconcat -f flv rtmp://...`.

    When loop=True we append a special directive at the end so the playlist
    restarts; this keeps the stream alive between rotations until the
    orchestrator decides to advance.
    """
    lines = ["ffconcat version 1.0"]
    for url in render_urls:
        if not url:
            continue
        # ffconcat supports remote URLs (https://...) but each `file` line\n        # has to be quoted. Single quotes work for our R2 URLs.\n        lines.append(f"file '{url}'")
    if loop and render_urls:
        # The standard concat-demuxer trick: re-list the first entry to keep
        # the player alive; the orchestrator should drive real rotation.
        lines.append(f"file '{render_urls[0]}'")
    return "\n".join(lines) + "\n"


def build_ffmpeg_command(
    playlist_path: str,
    rtmp_url: str,
    audio_extra_inputs: list[str] | None = None,
    overlay_text: str | None = None,
    bitrate_kbps: int = 4000,
    aspect_ratio: str = "9:16",
) -> list[str]:
    """Build the ffmpeg argv for the compositor's main process.

    The single-input version simply demuxes the concat playlist and pushes
    flv/RTMP. Extra audio inputs (e.g. background music) can be mixed in
    via `-filter_complex amix=`. Overlay text is burned with `drawtext`.
    """
    width, height = (1080, 1920) if aspect_ratio == "9:16" else (
        (1920, 1080) if aspect_ratio == "16:9" else (1080, 1080)
    )

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
    cmd += ["-re", "-f", "concat", "-safe", "0", "-i", playlist_path]

    # Mix in extra audio (background music, reactive voiceover) if present.
    audio_extra_inputs = audio_extra_inputs or []
    for src in audio_extra_inputs:
        cmd += ["-stream_loop", "-1", "-i", src]

    vf_chain = f"scale={width}:{height}:force_original_aspect_ratio=decrease," \
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    if overlay_text:
        # drawtext requires escaping single quotes in text.
        safe = overlay_text.replace("'", r"\'")
        vf_chain += (
            f",drawtext=text='{safe}':fontcolor=white:fontsize=42"
            f":borderw=3:bordercolor=black@0.6:x=40:y=40"
        )

    cmd += ["-vf", vf_chain]

    # Audio: if extra inputs are present, mix; otherwise use the playlist's audio.
    if audio_extra_inputs:
        amix_inputs = "[0:a]" + "".join(f"[{i+1}:a]" for i in range(len(audio_extra_inputs)))
        cmd += [
            "-filter_complex",
            f"{amix_inputs}amix=inputs={1 + len(audio_extra_inputs)}:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
        "-b:v", f"{bitrate_kbps}k", "-maxrate", f"{bitrate_kbps}k",
        "-bufsize", f"{bitrate_kbps * 2}k",
        "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
        "-f", "flv", rtmp_url,
    ]
    return cmd


__all__ = [
    "build_concat_playlist",
    "build_ffmpeg_command",
]
