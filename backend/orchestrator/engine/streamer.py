"""
RTMP Streaming Process Manager.
Manages FFmpeg subprocess for live streaming to TikTok.
Designed to run as a Celery task.
"""
import os
import signal
import subprocess
import asyncio
import tempfile
import logging
import json
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class StreamerProcess:
    """Manages an FFmpeg RTMP streaming subprocess."""

    def __init__(self, session_id: str, rtmp_url: str, cache_dir: str = "/tmp/stream_cache"):
        self.session_id = session_id
        self.rtmp_url = rtmp_url
        self.cache_dir = os.path.join(cache_dir, session_id)
        self.process: Optional[subprocess.Popen] = None
        self.is_running = False
        self.is_paused = False
        self._current_clip_path: Optional[str] = None
        self._clip_queue: list[str] = []
        self._crash_count = 0
        self._max_crashes = 5

        os.makedirs(self.cache_dir, exist_ok=True)

    def start(self, initial_clip_path: str):
        """Start FFmpeg process with the first clip."""
        from engine.compositor import build_stream_output_args

        self._current_clip_path = initial_clip_path

        cmd = [
            "ffmpeg",
            "-re",  # Read at native framerate
            "-i", initial_clip_path,
            *build_stream_output_args(self.rtmp_url),
        ]

        logger.info(json.dumps({
            "service": "streamer",
            "level": "info",
            "message": f"Starting FFmpeg for session {self.session_id}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
        }))

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,  # Create new process group
        )
        self.is_running = True

    def stop(self):
        """Stop FFmpeg gracefully, then forcefully if needed."""
        self.is_running = False
        self._kill_process()

    def _kill_process(self):
        """Kill FFmpeg process group to prevent zombies."""
        if self.process and self.process.returncode is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    self.process.wait(timeout=2)
                except (ProcessLookupError, OSError):
                    pass
            except Exception:
                pass

        logger.info(json.dumps({
            "service": "streamer",
            "level": "info",
            "message": f"FFmpeg process killed for session {self.session_id}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
        }))

    def feed_clip(self, clip_path: str):
        """Queue a clip to be fed to FFmpeg."""
        self._clip_queue.append(clip_path)

    def handle_crash(self) -> bool:
        """Handle FFmpeg crash. Returns True if should retry, False if max reached."""
        self._crash_count += 1
        logger.error(json.dumps({
            "service": "streamer",
            "level": "error",
            "message": f"FFmpeg crashed ({self._crash_count}/{self._max_crashes})",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
        }))
        return self._crash_count < self._max_crashes

    def cleanup(self):
        """Clean up cache directory and processes."""
        self._kill_process()
        # Clean up cached files
        if os.path.exists(self.cache_dir):
            for f in os.listdir(self.cache_dir):
                try:
                    os.remove(os.path.join(self.cache_dir, f))
                except OSError:
                    pass
            try:
                os.rmdir(self.cache_dir)
            except OSError:
                pass

    @property
    def crash_count(self) -> int:
        return self._crash_count
