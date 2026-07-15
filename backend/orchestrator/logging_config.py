"""Centralized logging — sends to Better Stack + Docker stdout."""

import logging
import json
from datetime import datetime, timezone

from config import settings

BETTERSTACK_TOKEN = settings.BETTERSTACK_TOKEN


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logs."""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Add extra fields if present
        for key in ("avatar_id", "user_id", "service", "step", "tier",
                     "duration_seconds", "status", "fallback_reason",
                     "input_duration", "voice_id", "job_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

class ConsoleFormatter(logging.Formatter):
    """Human-readable, line-by-line formatter for local dev console output."""
    def format(self, record):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        lines = [
            f"[{ts}] {record.levelname}",
            f"  location : {record.module}:{record.lineno} ({record.funcName})",
            f"  message  : {record.getMessage()}",
        ]

        for key in ("avatar_id", "user_id", "service", "step", "tier",
                     "duration_seconds", "status", "fallback_reason",
                     "input_duration", "voice_id", "job_id"):
            if hasattr(record, key):
                lines.append(f"  {key:<10}: {getattr(record, key)}")

        if record.exc_info:
            lines.append("  traceback:")
            for tb_line in self.formatException(record.exc_info).splitlines():
                lines.append(f"    {tb_line}")

        lines.append("")  # blank line between entries
        return "\n".join(lines)

def setup_logging():
    """Call once at app startup. Safe to call multiple times — won't add duplicate handlers."""
    root = logging.getLogger()

    if root.handlers:
        # Already configured (e.g. called from both main.py and tasks/__init__.py
        # in the same process) — skip to avoid duplicate log lines.
        return

    root.setLevel(logging.INFO)

    # Console handler (Docker stdout)
    console = logging.StreamHandler()
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    # Better Stack handler
    if BETTERSTACK_TOKEN:
        try:
            from logtail import LogtailHandler
            bt_handler = LogtailHandler(source_token=BETTERSTACK_TOKEN)
            root.addHandler(bt_handler)
        except ImportError:
            pass