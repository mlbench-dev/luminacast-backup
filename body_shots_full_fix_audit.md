# Body Shots Full Fix — Evidence Audit

**Date:** 2026-04-14
**Avatars investigated:** Maya (avt_c11495b83b64), Huyaya (avt_1edd71c8866f)
**VPS:** root@145.223.121.28, project at /opt/luminacast-omni
**Status:** Phase A complete — evidence gathered, NO code changes

---

## A1 — Production Code Paths

### Files involved

| File | Role |
|------|------|
| `backend/orchestrator/routers/avatar.py` (lines 2400-2830) | Main routes: `/ai/generate-body-shots`, `/ai/regenerate-body-shot` |
| `backend/orchestrator/services/qwen_body_shots_client.py` | Tier 1 self-hosted GPU client |
| `backend/orchestrator/services/ai_prompts.py` (lines 399-470) | Prompt templates for Stage 1 and Tier 3 |
| `backend/orchestrator/services/body_shot_validator.py` | MediaPipe validator (NOT used — LLM vision used instead) |

### Production code: ANGLES list

```python
ANGLES = [
    "front", "three_quarter_left", "three_quarter_right",
    "profile_left", "profile_right", "back",
]
```

### Production code: QWEN_ANGLE_PROMPTS (DEAD CODE — never read by active code path)

```python
QWEN_ANGLE_PROMPTS = {
    "front": "<sks> front view eye-level shot medium shot",
    "three_quarter_left": "<sks> front-left quarter eye-level shot medium shot",
    "three_quarter_right": "<sks> front-right quarter eye-level shot medium shot",
    "profile_left": "<sks> left side eye-level shot medium shot",
    "profile_right": "<sks> right side eye-level shot medium shot",
    "back": "<sks> back view eye-level shot medium shot",
}
```

**Note:** This dict is defined at avatar.py:2497 but is NEVER referenced by any code path. Tier 1 passes the `angle` string to the GPU server. Tier 2 uses `FAL_QWEN_ANGLES` numeric params. Tier 3 uses Kontext prompt templates.

### Production code: FAL_QWEN_ANGLES (Tier 2 — ACTIVE)

```python
FAL_QWEN_ANGLES = {
    "front":               {"horizontal_angle": 0,   "vertical_angle": 0},
    "three_quarter_left":  {"horizontal_angle": 315, "vertical_angle": 0},
    "three_quarter_right": {"horizontal_angle": 45,  "vertical_angle": 0},
    "profile_left":        {"horizontal_angle": 270, "vertical_angle": 0},
    "profile_right":       {"horizontal_angle": 90,  "vertical_angle": 0},
    "back":                {"horizontal_angle": 180, "vertical_angle": 0},
}
```

### Production code: Tier 2 fal_client.run_async call

```python
fal_angles = FAL_QWEN_ANGLES[angle]
fal_result = await fal_client.run_async(
    "fal-ai/qwen-image-edit-2511-multiple-angles",
    arguments={
        "image_urls": [canonical_url],
        "horizontal_angle": fal_angles["horizontal_angle"],
        "vertical_angle": fal_angles["vertical_angle"],
        "seed": locked_seed,
        "num_inference_steps": 28,
        "guidance_scale": 4.5,
        "output_format": "jpeg",
    },
)
```

### Production code: Stage 1 (Canonical Front)

```python
canonical_result = await fal_client.run_async(
    "fal-ai/flux-pro/v1.1-ultra",
    arguments={
        "prompt": canonical_prompt,  # text-only, body_description + style_hint
        "num_images": 1,
        "output_format": "jpeg",
        "seed": locked_seed,
        "aspect_ratio": "9:16",
    },
)
```

Stage 1 prompt template (`flux_body_canonical_front`):
```
Full body portrait, standing in a neutral relaxed pose, arms slightly away from body, feet visible, plain studio background, facing camera directly.
{body_description}
{style_hint}
Photorealistic, professional studio lighting, vertical 9:16 composition, sharp focus, 8K quality.
Clean seamless background, no text, no watermarks, no logos.
```

**CRITICAL FINDING:** Stage 1 is pure text-to-image via `fal-ai/flux-pro/v1.1-ultra`. The avatar's `face_ref_key` is checked as a precondition (line ~2435) but is NEVER passed to any generation call. The canonical front is generated from `body_description` text alone, producing a RANDOM person matching the text description.

### Production code: Reference image sourcing

For ALL three tiers, the reference image is `canonical_url` — the Stage 1 text-to-image output:
- **Tier 1** (Qwen self-hosted): `reference_image_url=canonical_url`
- **Tier 2** (fal.ai Qwen): `"image_urls": [canonical_url]`
- **Tier 3** (FLUX Kontext): `"image_urls": [canonical_url]`

The avatar's actual face (`face_ref_key`) is never sent to any image generation endpoint.

### 3-Tier Fallback Logic

1. **Tier 1** — Self-hosted Qwen on HOSTKEY GPU (currently failing — Tier 1 health check returns false)
2. **Tier 2** — fal.ai Qwen (`fal-ai/qwen-image-edit-2511-multiple-angles`) with numeric angles
3. **Tier 3** — FLUX Kontext legacy (`fal-ai/flux-pro/kontext`) with text prompts including `body_description`

### Regenerate route (separate code path)

`POST /ai/regenerate-body-shot` uses ONLY Tier 3 (FLUX Kontext). The Kontext prompts include identity-describing text like "Same person as the reference image, maintaining exact facial identity, clothing, build, and accessories." This is the old approach. The reference is still `canonical_url` from the original set.

---

## A2 — fal.ai Live API Schema

**Endpoint ID:** `fal-ai/qwen-image-edit-2511-multiple-angles`

### Input fields (16 total)

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `image_urls` | `list<string>` | **YES** | — |
| `horizontal_angle` | `float` | No | — |
| `vertical_angle` | `float` | No | — |
| `zoom` | `float` | No | 5 |
| `additional_prompt` | `string` | No | — |
| `lora_scale` | `float` | No | 1 |
| `image_size` | `ImageSize` or `Enum` | No | — |
| `guidance_scale` | `float` | No | 4.5 |
| `num_inference_steps` | `integer` | No | 28 |
| `acceleration` | `Enum` | No | "regular" |
| `negative_prompt` | `string` | No | "" |
| `seed` | `integer` | No | — |
| `sync_mode` | `boolean` | No | — |
| `enable_safety_checker` | `boolean` | No | true |
| `output_format` | `Enum` | No | "png" |
| `num_images` | `integer` | No | 1 |

### Key findings:

- **`image_urls`**: Array of plain URL strings (`["https://..."]`), NOT array of objects. Production code uses this correctly.
- **No top-level `prompt` field** — only `additional_prompt` for appending to auto-generated prompt. Production code correctly omits `prompt`.
- **No `identity_weight` / `reference_strength` / `ip_adapter_scale`** — the only strength param is `lora_scale` (default 1).
- **`<sks>` is auto-injected** — the endpoint constructs `<sks> [azimuth] [elevation] [distance]` from numeric params.
- **No structured angle enum** — uses raw float `horizontal_angle` (0-360) and `vertical_angle` (-30 to 90).

### Output schema

```json
{
  "images": [{"url": "...", "content_type": "...", "width": N, "height": N}],
  "seed": 42,
  "prompt": "<sks> front-left quarter view eye-level shot medium shot"
}
```

---

## A3 — LoRA HuggingFace Model Card

**Model:** `fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA`

### Trigger token format

`<sks> [azimuth] [elevation] [distance]` — order is mandatory.

### Azimuth tokens (8 positions)

| Degrees | Token |
|---------|-------|
| 0° | `front view` |
| 45° | `front-right quarter view` |
| 90° | `right side view` |
| 135° | `back-right quarter view` |
| 180° | `back view` |
| 225° | `back-left quarter view` |
| 270° | `left side view` |
| 315° | `front-left quarter view` |

### Elevation tokens

| Degrees | Token |
|---------|-------|
| -30° | `low-angle shot` |
| 0° | `eye-level shot` |
| 30° | `elevated shot` |
| 60° | `high-angle shot` |

### Distance tokens

| Factor | Token |
|--------|-------|
| x0.6 | `close-up` |
| x1.0 | `medium shot` |
| x1.8 | `wide shot` |

### Left/right convention

**Object-relative.** "left side view" at 270° = camera positioned to show the object's left side. Standard 3D azimuth convention.

### Identity preservation guidance

**None provided.** No guidance on minimal vs descriptive prompts.

### Author example prompts (verbatim)

```
<sks> front view eye-level shot medium shot
<sks> right side view high-angle shot close-up
<sks> back view low-angle shot wide shot
<sks> front-left quarter view elevated shot medium shot
```

### LoRA strength recommendation

0.8 - 1.0, start with 0.9.

---

## A4 — Reference Image Inspection

### Maya (avt_c11495b83b64) face_ref_key

`creators/usr_admin_8499fdd9/avatar/avt_c11495b83b64/face_ref.jpg`

**Visual description:** Blonde/silver-haired woman with blue/green eyes, glitter makeup on cheeks, futuristic/cyberpunk aesthetic, wearing dark reflective clothing. Close-up face shot.

### Maya canonical front (Stage 1 output for bss_ad44e63ef4bc)

`creators/usr_admin_8499fdd9/avatar/avt_c11495b83b64/body_shots/bss_ad44e63ef4bc/canonical.jpg`

**Visual description:** Brunette woman with brown eyes, no glitter, wearing cream blouse and charcoal trousers. Professional studio photo aesthetic. Full-body 9:16 standing pose.

### VERDICT: COMPLETE IDENTITY MISMATCH

The face_ref and canonical front are **clearly different people**:
- Hair: silver/blonde (face_ref) vs brown (canonical)
- Eyes: blue/green (face_ref) vs brown (canonical)
- Makeup: glitter sparkle (face_ref) vs natural (canonical)
- Aesthetic: cyberpunk (face_ref) vs professional studio (canonical)

**Root cause:** Stage 1 (`fal-ai/flux-pro/v1.1-ultra`) is text-to-image with NO face reference. It generated a random person matching the `body_description` text. The avatar's actual face was never injected.

---

## A5 — Two-Stage Pipeline Check

### Stage 1: EXISTS but BROKEN for identity

Stage 1 exists at avatar.py:2453-2482. It uses `fal-ai/flux-pro/v1.1-ultra` with:
- `prompt`: body_description + style_hint (text-only)
- `aspect_ratio`: "9:16"
- `seed`: locked_seed
- **NO face reference input** — `image_url`, `image_urls`, or any reference parameter is NOT passed

The `flux-pro/v1.1-ultra` endpoint is text-to-image only. To inject face identity, the code should use `fal-ai/flux-pro/kontext` (which accepts `image_urls` for reference) instead.

### Stage 2: EXISTS and working

Stage 2 at avatar.py:2542-2626. Uses 3-tier fallback with Qwen as primary. Uses `canonical_url` (Stage 1 output) as reference for all 6 angles. The stage itself works correctly — the problem is it receives the wrong reference image.

---

## A6 — Direct fal.ai API Tests

### Test A: Reproduce production 3/4 left vs 3/4 right

| Angle | h_angle | Auto-prompt | Result |
|-------|---------|-------------|--------|
| three_quarter_left | 315 | `<sks> front-left quarter view eye-level shot medium shot` | Very subtle left rotation |
| three_quarter_right | 45 | `<sks> front-right quarter view eye-level shot medium shot` | Very subtle right rotation |

**Observation:** Both 3/4 shots are very similar — nearly front-facing with only subtle angular differences. The LoRA at 45°/315° produces minimal rotation from front. This is a **LoRA limitation**, not a code bug. The angles ARE technically different (different auto-prompts, different outputs) but the visual distinction is weak at quarter-view.

### Test B: Profile and extreme angles

| Angle | h_angle | Auto-prompt | Result |
|-------|---------|-------------|--------|
| profile_left | 270 | `<sks> left side view eye-level shot medium shot` | Clear left profile, only left eye visible |
| profile_right | 90 | `<sks> right side view eye-level shot medium shot` | Clear right profile, only right eye visible |
| front | 0 | `<sks> front view eye-level shot medium shot` | Front-facing, both eyes visible |
| back | 180 | `<sks> back view eye-level shot medium shot` | Back of head, no face visible |

**Observation:** Profile, front, and back angles work correctly. Left/right sides are correct (not swapped). The angle differentiation issue is specifically with 3/4 views — the LoRA's 45°/315° positions don't produce enough rotation to be visually distinct from front.

### Test C: Identity preservation

Comparing canonical front (input) to Qwen outputs (all angles): the Qwen LoRA **excellently preserves** the identity of whoever is in the input reference. Same face shape, hair, clothing across all angles. The problem is NOT the LoRA losing identity — the problem is the wrong person is in the reference to begin with (Stage 1 generates random person).

**Test C verdict:** Qwen LoRA preserves identity well. Face swap (Phase E) is **likely not needed** if we fix Stage 1 to inject the avatar's actual face.

---

## A7 — Root Causes

### Root Cause 1: Angle Differentiation — LoRA limitation at quarter-view angles

**Category:** (d) LoRA limitation — 45°/315° produce insufficient visual rotation

The production code sends the correct `horizontal_angle` values and the endpoint auto-constructs the correct `<sks>` trigger tokens. The issue is that the Qwen LoRA at 45° and 315° azimuth (quarter views) doesn't produce enough visible rotation from front. Profile (90°/270°), back (180°), and front (0°) all work correctly.

**Fix approach:** No code change needed for the numeric angles — they're correct. However, we should clean up the dead `QWEN_ANGLE_PROMPTS` dict to avoid confusion. The 3/4 angle similarity is a LoRA limitation that cannot be fixed by code changes alone.

**Alternative:** We could try `additional_prompt` to reinforce the angle (e.g., "body turned significantly to the left, left shoulder prominent") but this risks fighting the LoRA's numeric angle control.

### Root Cause 2: Identity Not Preserved — Stage 1 is pure text-to-image with no face reference

**Category:** (iii) Two-stage pipeline exists but Stage 1 never injects face identity

Stage 1 uses `fal-ai/flux-pro/v1.1-ultra` which is a TEXT-TO-IMAGE endpoint. It has no `image_url` or `image_urls` parameter for face reference. The avatar's `face_ref_key` is checked as a precondition but never passed to any generation call.

**Fix:** Replace Stage 1's endpoint from `fal-ai/flux-pro/v1.1-ultra` (text-only) with `fal-ai/flux-pro/kontext` (accepts `image_urls` for reference image). Pass the avatar's face image as the reference so the canonical front preserves the avatar's actual face. The body description drives the clothing/body, while the face reference drives identity.

### Root Cause 3: Face Match Priority

**Decision:** Based on Test C, the Qwen LoRA preserves identity well from its input reference. If Stage 1 is fixed to produce a canonical front matching the avatar's face, all Qwen angles should inherit that identity. **Phase E (face swap) is likely NOT needed**, but Phase D visual verification is the gate.

### Summary of Root Causes

| # | Problem | Root Cause | Fix |
|---|---------|------------|-----|
| 1 | 3/4 angles near-identical | LoRA limitation at 45°/315° | Accept or try `additional_prompt` reinforcement |
| 2 | Identity not preserved | Stage 1 uses text-to-image, no face ref | Switch to `flux-pro/kontext` with `image_urls` |
| 3 | Face match to avatar | Stage 1 generates wrong person | Fixed by #2 |

---

## Phase A Gate Checklist

- [x] Exact production code paths and current prompts/arguments captured
- [x] fal.ai live API schema captured (16 input fields documented)
- [x] HuggingFace LoRA model card trigger tokens captured (8 azimuths x 4 elevations x 3 distances)
- [x] Reference image inspection (canonical front is random person, not avatar's face)
- [x] Two-stage pipeline check (Stage 1 exists but uses text-only endpoint)
- [x] Test A/B/C completed and images saved
- [x] Three root causes identified explicitly
- [x] Face match priority decision: face swap likely NOT needed if Stage 1 fixed
- [x] NO CODE CHANGES
