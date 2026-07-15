# GPU Worker Audit — Phase A

**Date:** 2026-04-14
**Server:** root@194.247.183.12 (HOSTKEY RTX 4090 24GB)
**Worker path:** /opt/gpu-worker/worker.py (1723 lines)
**Process:** uvicorn via systemd, single worker, port 7860

## Hardware

- **GPU:** NVIDIA GeForce RTX 4090
- **VRAM Total:** 24,564 MiB (24 GB)
- **VRAM Idle:** ~4,348 MiB used (likely cached allocations from prior model loads)

## Current Endpoints

| Endpoint | Method | Model(s) | VRAM Est. | Status |
|---|---|---|---|---|
| `/api/bs-roformer` | POST | BS-RoFormer + Whisper large-v3 | ~3GB + ~3GB | Active |
| `/api/fish-speech` | POST | (none — stub) | N/A | Stub (501) |
| `/api/infinitetalk` | POST | Wan2.1-14B DiT + wav2vec2 | ~14-20GB (CPU offload) | Active |
| `/api/upscale-video` | POST | Real-ESRGAN (+GFPGAN optional) | ~2GB (+2GB) | Active |
| `/api/musetalk-lipsync` | POST | MuseTalk (VAE+UNet+PE+Whisper) | ~4-8GB | Active |
| `/api/health` | GET | (none) | N/A | Active |
| `/api/music-generate` | POST | ACE-Step (subprocess) | ~8GB | Active |
| `/api/music-train-lora` | POST | ACE-Step trainer (subprocess) | ~12GB | Active |
| `/api/whisper-transcribe` | POST | WhisperX large-v3 + wav2vec2 | ~3GB | Active |
| `/api/pyannote-diarize` | POST | Pyannote speaker-diarization-3.1 | ~2GB | Active |

## Current Model Loading Pattern

- **Single asyncio.Lock (`gpu_lock`)** gates ALL endpoint handlers — only one request at a time
- **Global `current_model` + `model_instance`** — hot-swap pattern: check if target model matches, if not, unload current and load new
- **Whisper** cached separately (`_whisper_model` global) — kept alongside BS-RoFormer since both fit
- **WhisperX** cached separately (`_whisperx_model` + alignment models) — separate from the main swap chain
- **Pyannote** cached separately (`_pyannote_pipeline`) — separate from the main swap chain
- **InfiniteTalk** unloads Whisper before loading (needs full VRAM)
- **MuseTalk** unloads Whisper before loading
- **ACE-Step** runs as subprocess in separate venv (avoids torch version conflicts)

## Current Models on Disk

```
/opt/gpu-worker/models/
├── GFPGANv1.4.pth (348 MB)
├── RealESRGAN_x4plus.pth (67 MB)
└── (InfiniteTalk, Wan2.1, wav2vec2, MuseTalk models at separate paths)
```

Additional paths:
- `/opt/gpu-worker/InfiniteTalk/` — InfiniteTalk repo
- `/opt/gpu-worker/models/Wan2.1-I2V-14B-480P/` — Wan base model
- `/opt/gpu-worker/models/chinese-wav2vec2-base/` — wav2vec2
- `/opt/gpu-worker/models/infinitetalk/` — InfiniteTalk weights
- `/opt/musetalk/` — MuseTalk installation

## Issues Found

1. **`_update_job()` called but never defined** — used in upscale pipeline (lines 749-892). Will cause NameError if upscale endpoint is called. Must define or stub it.
2. **No queue** — `gpu_lock` serializes but doesn't queue. During InfiniteTalk (5+ min), all other requests block without feedback.
3. **No TTL** — models stay resident forever until another model needs the slot.
4. **No VRAM budget tracking** — each model loader calls `_unload_current()` blindly without checking available headroom.
5. **WhisperX/Pyannote/Whisper bypass the hot-swap chain** — they have separate globals, could theoretically conflict with the main model.

## Model Size Classification (for router)

| Model | Size Class | Est. VRAM | Notes |
|---|---|---|---|
| bs_roformer | SMALL | ~3 GB | Co-resident with whisper |
| whisper | SMALL | ~3 GB | Currently cached separately |
| whisperx | SMALL | ~3 GB | Currently cached separately |
| pyannote | SMALL | ~2 GB | Currently cached separately |
| realesrgan | SMALL | ~2 GB | +2GB with GFPGAN |
| fish_speech | SMALL | ~4 GB | Stub, not yet implemented |
| musetalk | MEDIUM | ~8 GB | Needs exclusive big-model slot |
| infinitetalk | BIG | ~14-20 GB | CPU offload on 4090, exclusive |
| ace_step | BIG | ~8-12 GB | Runs as subprocess |
| qwen_image_edit | BIG | ~20 GB | NEW — to be added |
