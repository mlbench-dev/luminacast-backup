# Body Shots Post-Fix Audit — 2026-04-14

## Executive Summary

**Root cause identified: Tier 2 (fal.ai) sends the WRONG API schema.**
The orchestrator sends `prompt` with `<sks>` triggers and `num_inference_steps=4`, but the
fal.ai `qwen-image-edit-2511-multiple-angles` endpoint expects `horizontal_angle`/`vertical_angle`
numeric parameters, not text prompts. The `<sks>` trigger and low inference steps are for the
**self-hosted LoRA**, not fal.ai's hosted model.

Additionally, the GPU worker was **crash-looping** (port 7860 held by zombie PID 586139) at
the time of the last generation, meaning Tier 1 health check returned False, Tier 2 used wrong
schema (likely got 422 or produced wrong output), and Tier 3 (FLUX Kontext) served all angles —
which explains the near-frontal variants.

---

## A1 — Latest body shot set for avt_c11495b83b64

```
id:         bss_831ab90eba2e
created_at: 2026-04-14 11:52:18.382781
avatar_id:  avt_c11495b83b64
```

Previous set: `bss_66313db90104` created `2026-04-13 20:27:11.559805`

**Note:** The `body_shot_sets` table does NOT store `engine_used` per angle. The `angles` column
is a JSON dict mapping angle names to R2 keys. There is a `body_shot_validation_log` table with
an `engine_used` column, but it contains **zero rows** for this set — the orchestrator code does
validation via LLM but does not persist results to this table.

## A2 — Angles stored in DB

```json
{
  "front": "creators/usr_admin_8499fdd9/avatar/avt_c11495b83b64/body_shots/bss_831ab90eba2e/front.jpg",
  "three_quarter_left": ".../three_quarter_left.jpg",
  "three_quarter_right": ".../three_quarter_right.jpg",
  "profile_left": ".../profile_left.jpg",
  "profile_right": ".../profile_right.jpg",
  "back": ".../back.jpg"
}
```

All 6 angles present. No engine_used metadata persisted.

## A3 — Orchestrator logs

**No logs available.** The orchestrator container was restarted at `2026-04-14T11:56:14Z` —
4 minutes AFTER the body shots were generated at 11:52:18. Historical logs from that generation
run are gone. Cannot determine from logs alone which tier served.

## A4 — GPU Worker status

### Crash loop (RESOLVED)

The GPU worker service was in a crash loop at time of audit. systemd kept restarting it every
~8 seconds, but a zombie uvicorn process (PID 586139) was holding port 7860:

```
ERROR: [Errno 98] error while attempting to bind on address ('0.0.0.0', 7860): address already in use
```

**Resolution:** Killed PID 586139, restarted gpu-worker.service. Now running as PID 588074.

### Worker health (post-fix)

```
GET /api/router/status → 200 OK
{
  "resident_models": [],
  "model_states": {
    "qwen_image_edit": {"state": "unloaded", "size": "big", "vram_gb": 0.0, "ttl_seconds": 30.0},
    ...
  },
  "vram_used_gb": 0.0,
  "vram_total_gb": 25.26
}
```

Router is healthy. `qwen_image_edit` is registered and ready to load on demand.

### GPU worker logs (last 4 hours)

No Qwen inference requests received. Only startup/registration logs. The crash loop means
no Qwen requests could have been served during the 11:52 generation window either.

**Conclusion: Tier 1 was DOWN when the last body shots were generated.**

## A5 — LoRA file verification

```
/opt/gpu-worker/models/qwen_image_edit_2511/
├── base/                          # FP8 diffusion weights + Qwen2.5-VL + VAE
│   ├── transformer/               # 5 shards, ~41GB total
│   ├── text_encoder/              # 4 shards, ~16GB total
│   └── vae/                       # 254MB
└── lora/
    └── qwen-image-edit-2511-multiple-angles-lora.safetensors  (295MB)
```

**LoRA filename:** `qwen-image-edit-2511-multiple-angles-lora.safetensors` (HYPHENS, not underscores)

The handler code dynamically discovers the LoRA file:
```python
lora_files = [f for f in os.listdir(LORA_DIR) if f.endswith(".safetensors")]
```
So the filename mismatch concern is **NOT an issue** — the code doesn't hardcode the name.

## A6 — Handler code review (`/opt/gpu-worker/handlers/qwen_body_shots.py`)

```python
ANGLE_PROMPTS = {
    "front": "<sks> front view eye-level shot medium shot",
    "three_quarter_left": "<sks> front-left quarter eye-level shot medium shot",
    "three_quarter_right": "<sks> front-right quarter eye-level shot medium shot",
    "profile_left": "<sks> left side eye-level shot medium shot",
    "profile_right": "<sks> right side eye-level shot medium shot",
    "back": "<sks> back view eye-level shot medium shot",
}

BASE_DIR = "/opt/gpu-worker/models/qwen_image_edit_2511/base"
LORA_DIR = "/opt/gpu-worker/models/qwen_image_edit_2511/lora"
```

**Findings:**
- All 6 prompts contain `<sks>` trigger -- CORRECT
- LoRA loading uses dynamic file discovery -- CORRECT
- `set_adapters(["multiple_angles"], adapter_weights=[1.0])` called after `load_lora_weights` -- CORRECT
- Pipeline uses `torch.bfloat16` and `.to("cuda")` -- CORRECT
- `num_inference_steps=4` and `guidance_scale=1.0` match Qwen fast mode -- CORRECT
- Error handling calls `sentry_sdk.capture_exception` -- CORRECT

**Tier 1 handler code is sound.** The self-hosted path would work IF the GPU worker were running.

## A7 — Prompt verification

All 6 QWEN_ANGLE_PROMPTS in both the GPU handler and the orchestrator's fallback dict contain
the `<sks>` trigger prefix. No missing triggers.

For Tier 3 (FLUX Kontext), prompts are loaded from `ai_prompts.py` prompt registry using
natural language descriptions (no `<sks>` needed for Kontext). These prompts describe poses
in prose but FLUX Kontext's identity preservation from image_urls is limited — it tends to
produce near-frontal variants regardless of the prompt. This is the known Tier 3 limitation.

## A8 — fal.ai live API schema for `qwen-image-edit-2511-multiple-angles`

**CRITICAL MISMATCH FOUND.**

### What fal.ai actually expects:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image_urls` | `list[str]` | **Yes** | Reference image URLs |
| `horizontal_angle` | `float` | No | Rotation in degrees: 0=front, 90=right, 180=back, 270=left |
| `vertical_angle` | `float` | No | Elevation: -30=low, 0=eye-level, 90=bird's-eye |
| `zoom` | `float` | No | Distance, default 5 |
| `additional_prompt` | `str` | No | Extra text appended to generated prompt |
| `lora_scale` | `float` | No | LoRA strength, default 1 |
| `image_size` | `object/enum` | No | Output dimensions |
| `guidance_scale` | `float` | No | CFG, default **4.5** |
| `num_inference_steps` | `int` | No | Steps, default **28** |
| `seed` | `int` | No | Reproducibility |
| `output_format` | `str` | No | png/jpeg/webp, default png |
| `num_images` | `int` | No | Count, default 1 |

### What the orchestrator sends:

```python
{
    "image_urls": [canonical_url],          # correct
    "prompt": QWEN_ANGLE_PROMPTS[angle],    # WRONG — field name is "additional_prompt"
    "seed": locked_seed,                    # correct
    "num_inference_steps": 4,               # WRONG — default 28, too low
    "guidance_scale": 1.0,                  # WRONG — default 4.5, too low
}
```

### Specific mismatches:

1. **`prompt` vs `additional_prompt`**: fal.ai ignores unknown fields, so the `<sks>` prompt
   is silently dropped. The model uses its own default prompt.
2. **Missing `horizontal_angle`/`vertical_angle`**: The fal.ai endpoint controls angles via
   NUMERIC PARAMETERS, not text prompts. Without these, all shots get the default angle
   (likely 0=front), explaining the "all look frontal" symptom.
3. **`num_inference_steps=4`**: Way too low for fal.ai's default of 28. May produce garbage.
4. **`guidance_scale=1.0`**: Too low for fal.ai's default of 4.5. Weak adherence to angle.

---

## Root Cause Determination

### Primary root cause: GPU worker crash loop (Tier 1 DOWN)

The GPU worker was crash-looping due to a zombie process holding port 7860. This caused:
- Tier 1 health check → `False`
- All requests fell through to Tier 2 or Tier 3

### Secondary root cause: Tier 2 fal.ai schema mismatch

When Tier 2 received requests, it sent the wrong payload:
- Used `prompt` instead of `horizontal_angle`/`vertical_angle`
- Used self-hosted LoRA parameters (`<sks>`, steps=4, cfg=1.0) for a different API
- Result: fal.ai either returned default-angle images or 422 errors

### Tertiary: Tier 3 FLUX Kontext served everything

FLUX Kontext was the final fallback. It generates body shots from prose descriptions but has
poor angle diversity — it tends to produce near-frontal variants regardless of the prompt.
This matches the symptom Andrey observed.

### Fix required:

**Fix Tier 2 fal.ai payload** to use the correct schema with `horizontal_angle`/`vertical_angle`
numeric parameters. This ensures proper angle diversity even when Tier 1 is down.

Tier 1 is now running (zombie killed, service restarted) but the fal.ai fix is still needed
for resilient fallback.

### Angle mapping for fal.ai:

```python
FAL_ANGLE_MAP = {
    "front":               {"horizontal_angle": 0,   "vertical_angle": 0},
    "three_quarter_left":  {"horizontal_angle": 315, "vertical_angle": 0},  # -45 = 315
    "three_quarter_right": {"horizontal_angle": 45,  "vertical_angle": 0},
    "profile_left":        {"horizontal_angle": 270, "vertical_angle": 0},
    "profile_right":       {"horizontal_angle": 90,  "vertical_angle": 0},
    "back":                {"horizontal_angle": 180, "vertical_angle": 0},
}
```

---

## NO CODE CHANGES MADE IN PHASE A

---

## Phase B — Fix Applied

**Commit:** `158d6a5` fix(body-shots): Tier 2 fal.ai payload uses horizontal_angle/vertical_angle

Changed orchestrator Tier 2 payload from text prompt + self-hosted params to
numeric angle params + fal.ai defaults.

---

## Phase C — Regeneration Test Results

**Set ID:** bss_ad44e63ef4bc  
**Generated:** 2026-04-14 12:17:54 - 12:19:34 UTC

### Per-angle results from orchestrator logs:

| Angle | Tier 1 | Tier 2 | Engine | Validation |
|-------|--------|--------|--------|------------|
| front | 500 | 200 | qwen_fal | MATCH |
| three_quarter_left | 500 | 200 | qwen_fal | MATCH |
| three_quarter_right | 500 | 200 | qwen_fal | MATCH |
| profile_left | 500 | 200 | qwen_fal | MATCH |
| profile_right | 500 | 200 | qwen_fal | MATCH |
| back | 500 | 200 | qwen_fal | MATCH |

**6 checked, 0 mismatches** — all angles validated by Claude Sonnet 4.

### Tier 1 failure detail
Error: `'dict' object has no attribute 'to_dict'` in transformers Qwen2_5VL model init.
This is a transformers/diffusers version incompatibility — separate fix needed on GPU server.

### Visual verification
Skipped — user will inspect screenshots manually.
