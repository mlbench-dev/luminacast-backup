"""Seed R2 with placeholder ambience-bed loops.

Run once per environment:
  docker compose exec orchestrator python scripts/seed_ambience_library.py

These are ffmpeg-synthesised stand-ins (filtered noise) so the mixing path can
be exercised end to end. Replace them with licensed seamless loop recordings
for production — the names + lengths are the contract in
``services/ambience_library`` (AMBIENCE_CATALOG).
"""
import asyncio
import os
import subprocess
import sys
import tempfile

# Ensure /app is on sys.path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.r2_storage import get_r2_storage_service
from services.ambience_library import AMBIENCE_CATALOG


# lavfi source chain per catalog name — a rough sonic stand-in, NOT the real
# atmosphere. brown/pink noise shaped with a low-pass gives a "bed" feel;
# the real files replace these 1:1 by name.
_LAVFI_BY_NAME: dict[str, str] = {
    "room_tone":    "anoisesrc=color=brown:amplitude=0.06,lowpass=f=400",
    "office_hum":   "anoisesrc=color=brown:amplitude=0.05,lowpass=f=300,highpass=f=60",
    "cafe_murmur":  "anoisesrc=color=pink:amplitude=0.08,lowpass=f=900,highpass=f=120",
    "city_street":  "anoisesrc=color=brown:amplitude=0.10,lowpass=f=1200",
    "wind_soft":    "anoisesrc=color=brown:amplitude=0.12,lowpass=f=700",
    "nature_birds": "anoisesrc=color=pink:amplitude=0.07,lowpass=f=2000,highpass=f=300",
    "ocean_waves":  "anoisesrc=color=brown:amplitude=0.11,lowpass=f=600",
}
_DEFAULT_LAVFI = "anoisesrc=color=brown:amplitude=0.08,lowpass=f=800"


def _placeholder_cmd(name: str, duration_s: float) -> list[str]:
    src = _LAVFI_BY_NAME.get(name, _DEFAULT_LAVFI)
    # Short fades top & tail so a naive loop doesn't click at the seam.
    # anoisesrc runs forever; -t bounds the output (embedding :duration= in the
    # lavfi string attaches it to the trailing filter, which has no such option).
    fade_st = max(duration_s - 0.3, 0.0)
    return [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", src,
        "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={fade_st}:d=0.3",
        "-t", f"{duration_s}",
        "-ar", "44100", "-ac", "2",
    ]


AMBIENCE_SPECS = [
    {"name": e.name, "cmd": _placeholder_cmd(e.name, e.loop_s)}
    for e in AMBIENCE_CATALOG.values()
]


async def main():
    r2 = get_r2_storage_service()
    uploaded = 0
    skipped = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for spec in AMBIENCE_SPECS:
            name = spec["name"]
            key = f"ambience/{name}.wav"
            local_path = os.path.join(tmpdir, f"{name}.wav")

            try:
                existing = await r2.head_object(key)
                if existing:
                    print(f"  skip {name} (already exists)")
                    skipped += 1
                    continue
            except Exception:
                pass

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
