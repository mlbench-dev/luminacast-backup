"""Seed R2 with royalty-free SFX clips.

Run once per environment:
  docker compose exec orchestrator python scripts/seed_sfx_library.py

For initial seed, generate simple tones with ffmpeg.
Replace with licensed CC0 files for production.
"""
import asyncio
import os
import subprocess
import sys
import tempfile

# Ensure /app is on sys.path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.r2_storage import get_r2_storage_service
from services.sfx_library import SFX_CATALOG


def _placeholder_cmd(duration_s: float) -> list[str]:
    """Generate a short fade-out tone as a stand-in clip of the right length.

    Replace these with licensed CC0 files for production; the names + lengths
    come from services/sfx_library (the SFX_CATALOG contract).
    """
    fade_st = max(duration_s - 0.1, 0.0)
    return [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=880:duration={duration_s}",
        "-af", f"afade=t=out:st={fade_st}:d=0.1", "-ar", "44100", "-ac", "2",
    ]


# One clip per catalog entry — keep names in lockstep with services/sfx_library.
SFX_SPECS = [
    {"name": entry.name, "cmd": _placeholder_cmd(entry.duration_s)}
    for entry in SFX_CATALOG.values()
]


async def main():
    r2 = get_r2_storage_service()
    uploaded = 0
    skipped = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for spec in SFX_SPECS:
            name = spec["name"]
            key = f"sfx/{name}.wav"
            local_path = os.path.join(tmpdir, f"{name}.wav")

            # Check if already exists
            try:
                existing = await r2.head_object(key)
                if existing:
                    print(f"  skip {name} (already exists)")
                    skipped += 1
                    continue
            except Exception:
                pass

            # Generate
            cmd = spec["cmd"] + [local_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  FAIL {name}: {result.stderr[-200:]}")
                continue

            with open(local_path, "rb") as f:
                data = f.read()
            await r2.upload_bytes(data, key, "audio/wav")
            print(f"  ok   {name} ({len(data)} bytes)")
            uploaded += 1

    print(f"\nDone: {uploaded} uploaded, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(main())
