"""Seed R2 with ambience-bed loops.

Two modes:

  # 1. Synthetic stand-ins (filtered noise) — exercises the mixing path only.
  docker compose exec orchestrator python scripts/seed_ambience_library.py

  # 2. Real recordings — point at a folder of licensed files. For each catalog
  #    name it uploads <name>.<ext> from that folder (any of
  #    wav/mp3/m4a/aac/ogg/opus/flac), transcoded to 44.1 kHz stereo WAV.
  #    Names with no file fall back to a synthetic stand-in.
  docker compose cp ./sounds orchestrator:/tmp/amb_src
  docker compose exec orchestrator python scripts/seed_ambience_library.py \
      --from-dir /tmp/amb_src --start 5 --max-len 45 --loop-xfade 2 --force

Flags:
  --from-dir PATH   folder of real files (or env AMBIENCE_SRC_DIR)
  --start SEC       skip the first SEC seconds of each real clip (default 0) —
                    drops intros / fade-ins
  --max-len SEC     trim each real clip to SEC seconds (default 0 = keep whole).
                    Not required: the renderer loops + trims to each scene, so a
                    long file just never repeats. Use this only to keep R2 small.
  --loop-xfade SEC  overlap the (trimmed) clip's tail onto its head by SEC
                    seconds so it loops seamlessly under -stream_loop (0 = off).
                    Pointless on a file longer than any scene — it never loops.
  --force           re-upload even if the R2 object already exists

The names + intent are the contract in ``services/ambience_library``
(AMBIENCE_CATALOG). R2 key per entry: ``ambience/<name>.wav``.

Where to get real files: freesound.org (filter to CC0), Pixabay Audio,
Zapsplat, or a paid pack (Epidemic Sound / Artlist). Length doesn't matter —
grab whatever sounds right; --start/--max-len/--loop-xfade shape it here.
"""
import argparse
import asyncio
import os
import subprocess
import sys
import tempfile

# Ensure /app is on sys.path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.r2_storage import get_r2_storage_service
from services.ambience_library import AMBIENCE_CATALOG

_SRC_EXTS = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac")


# lavfi source graph per catalog name — a rough sonic stand-in, NOT the real
# atmosphere, but shaped so the seven are audibly distinct while testing:
# low hum tones under room/office/city, slow tremolo "swells" for wind/ocean,
# a fast bright tremolo for birds, speech-band murmur for cafe. The real
# licensed loops replace these 1:1 by name (same R2 key).
_LAVFI_BY_NAME: dict[str, str] = {
    "room_tone":    "anoisesrc=c=brown:a=0.05[n];sine=f=60:b=0[s];[n][s]amix=inputs=2:weights=1 0.06:normalize=0,lowpass=f=350",
    "office_hum":   "anoisesrc=c=brown:a=0.04[n];sine=f=120:b=0[s];[n][s]amix=inputs=2:weights=1 0.10:normalize=0,highpass=f=60,lowpass=f=900",
    "cafe_murmur":  "anoisesrc=c=pink:a=0.08,highpass=f=180,lowpass=f=1100,tremolo=f=0.5:d=0.5",
    "city_street":  "anoisesrc=c=brown:a=0.10[n];sine=f=70:b=0[s];[n][s]amix=inputs=2:weights=1 0.12:normalize=0,lowpass=f=1400",
    "wind_soft":    "anoisesrc=c=brown:a=0.13,lowpass=f=700,tremolo=f=0.18:d=0.7",
    "nature_birds": "anoisesrc=c=pink:a=0.06[n];sine=f=2200:b=0[s];[n][s]amix=inputs=2:weights=1 0.05:normalize=0,highpass=f=400,lowpass=f=6000,tremolo=f=3.0:d=0.6",
    "ocean_waves":  "anoisesrc=c=brown:a=0.12,lowpass=f=550,tremolo=f=0.1:d=0.9",
}
_DEFAULT_LAVFI = "anoisesrc=c=brown:a=0.08,lowpass=f=800"


def _synthesize(name: str, duration_s: float, out_path: str) -> bool:
    src = _LAVFI_BY_NAME.get(name, _DEFAULT_LAVFI)
    fade_st = max(duration_s - 0.3, 0.0)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", src,
        "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={fade_st}:d=0.3",
        "-t", f"{duration_s}",
        "-ar", "44100", "-ac", "2", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  FAIL {name} (synth): {r.stderr[-200:]}")
        return False
    return True


def _probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _run(cmd: list[str]) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "")[-200:]


def _looks_like_audio(path: str, *, min_s: float = 0.8) -> bool:
    """Guard against ffmpeg exiting 0 while writing an empty file (older
    builds do this on a filtergraph they can't run)."""
    try:
        if os.path.getsize(path) < 2048:
            return False
    except OSError:
        return False
    return _probe_duration(path) >= min_s


def _prepare_real(
    src_path: str, out_path: str, *,
    start: float = 0.0, max_len: float = 0.0, loop_xfade: float = 0.0,
) -> bool:
    """Turn a real recording into a 44.1 kHz stereo WAV bed:
      1. optional trim — skip ``start`` s, keep ``max_len`` s
      2. optional seamless loop — overlap the tail onto the head by
         ``loop_xfade`` s so -stream_loop -1 doesn't click
    Steps that aren't requested are skipped."""
    workdir = os.path.dirname(out_path)
    stage = os.path.join(workdir, "_stage_" + os.path.basename(out_path))

    # 1. Trim / transcode to the staging WAV.
    trim_cmd = ["ffmpeg", "-y"]
    if start > 0:
        trim_cmd += ["-ss", f"{start:.3f}"]
    trim_cmd += ["-i", src_path]
    if max_len > 0:
        trim_cmd += ["-t", f"{max_len:.3f}"]
    trim_cmd += ["-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", stage]
    ok, err = _run(trim_cmd)
    if not ok or not _looks_like_audio(stage):
        print(f"  FAIL trim/transcode: {err or 'empty output'}")
        return False

    # 2. Seamless-loop crossfade, if asked and the clip is long enough.
    if loop_xfade > 0:
        dur = _probe_duration(stage)
        x = min(loop_xfade, max(0.0, dur / 2.0 - 0.1))
        if x > 0.05:
            tail = dur - x
            # asplit is REQUIRED — feeding [0:a] into two chains only
            # auto-splits on newer ffmpeg; older builds silently produce an
            # empty file (exit 0). c1/c2=tri keeps constant power at the seam.
            fc = (
                f"[0:a]asplit=2[a0][a1];"
                f"[a0]atrim=start=0:end={tail:.3f},asetpts=PTS-STARTPTS[b];"
                f"[a1]atrim=start={tail:.3f},asetpts=PTS-STARTPTS[t];"
                f"[t][b]acrossfade=d={x:.3f}:c1=tri:c2=tri[o]"
            )
            ok, err = _run([
                "ffmpeg", "-y", "-i", stage,
                "-filter_complex", fc, "-map", "[o]",
                "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", out_path,
            ])
            if ok and _looks_like_audio(out_path):
                os.remove(stage)
                return True
            print(f"  warn loop-xfade failed, using un-looped clip: {err or 'empty output'}")

    os.replace(stage, out_path)
    return _looks_like_audio(out_path)


def _find_source(src_dir: str, name: str) -> str | None:
    if not src_dir:
        return None
    for ext in _SRC_EXTS:
        p = os.path.join(src_dir, f"{name}{ext}")
        if os.path.isfile(p):
            return p
    return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", default=os.getenv("AMBIENCE_SRC_DIR", ""))
    ap.add_argument("--start", type=float, default=float(os.getenv("AMBIENCE_START", "0") or 0))
    ap.add_argument("--max-len", type=float, default=float(os.getenv("AMBIENCE_MAX_LEN", "0") or 0))
    ap.add_argument("--loop-xfade", type=float, default=float(os.getenv("AMBIENCE_LOOP_XFADE", "0") or 0))
    ap.add_argument("--force", action="store_true",
                    default=os.getenv("SEED_FORCE", "").strip().lower() in {"1", "true", "yes"})
    args = ap.parse_args()

    src_dir = args.from_dir.strip()
    if src_dir and not os.path.isdir(src_dir):
        print(f"--from-dir {src_dir!r} is not a directory")
        sys.exit(1)
    if src_dir:
        print(
            f"Real sources from: {src_dir} "
            f"(start={args.start}s max-len={args.max_len or 'full'}s "
            f"loop-xfade={args.loop_xfade}s)"
        )

    r2 = get_r2_storage_service()
    uploaded_real = 0
    uploaded_synth = 0
    skipped = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for entry in AMBIENCE_CATALOG.values():
            name = entry.name
            key = f"ambience/{name}.wav"
            out_path = os.path.join(tmpdir, f"{name}.wav")

            if not args.force:
                try:
                    if await r2.head_object(key):
                        print(f"  skip {name} (already exists)")
                        skipped += 1
                        continue
                except Exception:
                    pass

            real = _find_source(src_dir, name)
            if real:
                if not _prepare_real(
                    real, out_path,
                    start=args.start, max_len=args.max_len, loop_xfade=args.loop_xfade,
                ):
                    continue
                kind = "real"
            else:
                if not _synthesize(name, entry.loop_s, out_path):
                    continue
                kind = "synth"

            with open(out_path, "rb") as f:
                data = f.read()
            await r2.upload_bytes(data, key, "audio/wav")
            print(f"  ok   {name:<13} [{kind}] ({len(data)} bytes)")
            if kind == "real":
                uploaded_real += 1
            else:
                uploaded_synth += 1

    print(f"\nDone: {uploaded_real} real, {uploaded_synth} synthetic, {skipped} skipped")
    if uploaded_synth and src_dir:
        missing = [
            e.name for e in AMBIENCE_CATALOG.values()
            if not _find_source(src_dir, e.name)
        ]
        print(f"No real file found for: {', '.join(missing)} "
              f"(expected <name>{{{','.join(_SRC_EXTS)}}} in {src_dir})")


if __name__ == "__main__":
    asyncio.run(main())
