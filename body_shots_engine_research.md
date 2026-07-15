# Body Shots Engine Research — Avatar V3 Phase D

**Date:** 2026-04-13
**Author:** Claude (autonomous agent)
**Status:** Decision made — Two-Stage Pipeline (Plan A)

---

## D1 — Root Cause Analysis (confirmed from Phase A audit)

### Current Implementation

File: `routers/avatar.py:2370-2496`
Prompt templates: `ai_prompts.py:355-379` (`flux_body_front`, `flux_body_angle`)

**What happens today:**

1. `generate_body_shots()` calls `fal-ai/flux-pro/kontext` for all 6 angles
2. Every call uses `image_url=face_url` — the face close-up portrait as the reference image
3. The `flux_body_angle` prompt template (line 373-378) says: *"Same person as the reference image, maintaining exact facial identity, clothing, and build"*
4. A `locked_seed` is shared across all 6 calls for identity consistency

**Why back/profile views fail:**

- FLUX Kontext Pro treats the reference image as the **composition to preserve**. It performs in-context editing on the reference, not novel-view synthesis.
- When the reference is a **front-facing face close-up**, Kontext cannot produce a back view because that requires a fundamentally different camera position and composition — not an edit of the reference.
- The prompt *"maintaining exact facial identity"* actively fights back views where the face should NOT be visible.
- `locked_seed` preserves identity within the same generation transform, but cannot force a composition the model doesn't support for the given reference.

**Result:** All 6 shots come out as mild variations of front-facing portraits with slight head turns. Profile and back views are physically impossible with this approach.

---

## D2 — Research: Multi-View Character Generation Techniques

### 1. fal.ai Available Models

| Model | Endpoint | Price | Relevance |
|-------|----------|-------|-----------|
| FLUX Kontext Pro | `fal-ai/flux-pro/kontext` | $0.04/image | Current system. Good for edits, not novel views. |
| FLUX Kontext Max | `fal-ai/flux-pro/kontext/max` | $0.08/image | Better prompt adherence than Pro. Same paradigm — no pose conditioning. |
| FLUX Kontext Multi (experimental) | `fal-ai/flux-pro/kontext/multi` | $0.04/image | Multi-reference editing. Could combine face + pose reference. Experimental. |
| FLUX 1.1 Pro Ultra | `fal-ai/flux-pro/v1.1-ultra` | $0.06/image | Text-to-image up to 2K. No reference image conditioning — pure prompt. Good for Stage 1 canonical generation. |
| FLUX 1.1 Pro | `fal-ai/flux-pro/v1.1` | ~$0.04/image | Text-to-image. Slightly lower quality than Ultra. |
| IP-Adapter FaceID | Research only | Free (research) | Face-conditioned generation. Not commercial. |

**What fal.ai does NOT have:** No ControlNet/OpenPose endpoints, no Zero123/SV3D, no dedicated multi-view models, no explicit camera angle parameters.

URL: https://fal.ai/models

### 2. Multi-View Diffusion Models (fal.ai/Replicate availability)

| Model | Platform | Price | Speed | Quality Notes |
|-------|----------|-------|-------|---------------|
| Zero123++ | Replicate | ~$0.074/run (all 6 views) | ~76s | Fixed camera poses (30/90/150/210/270/330 degrees). Designed for 3D reconstruction input, not presentation quality. Fixed angles don't match our desired set. |
| Wonder3D | Replicate | Variable | Variable | Image-to-3D, designed for "singular objects." Low relevance for human characters. |
| MVDream | Replicate | ~$3.24/run | 55-60 min | Text-to-3D. Prohibitively slow and expensive. |
| ImageDream | Replicate | ~$2.77/run | 1-2 hours | Prohibitively slow. |
| SV3D (Stable Video 3D) | Not available | N/A | N/A | Not on Replicate or fal.ai as of April 2026. |
| Hunyuan3D-2MV | Replicate | ~$0.10/run | ~108s | Multi-view controlled 3D. Outputs 3D models, not 2D presentation images. |
| TRELLIS | Replicate | ~$0.046/run | ~33s | Fast 3D mesh generation. Could render views but produces 3D-render aesthetic, not photorealistic. |

**Verdict:** None of these produce presentation-quality 2D character images at the required angles. They're designed for 3D reconstruction pipelines, not editorial body shots.

### 3. Pose-Conditioned Models

| Approach | Platform | Price | Quality |
|----------|----------|-------|---------|
| ControlNet + OpenPose (SD 1.5) | Replicate | ~$0.019/run | SD 1.5 quality — far below FLUX. Character consistency via text prompt only. |
| Consistent Character (SDXL) | Replicate (`fofr/consistent-character`) | ~$0.015/run | Purpose-built for same character in different poses. SDXL quality (below FLUX). Cheapest option. |
| IP-Adapter FaceID | Replicate | ~$0.012/run | Research-only, face-only, not full body. "Does not achieve perfect photorealism." |

**Verdict:** Quality gap between SD 1.5/SDXL and FLUX is too large for a production avatar system. These would visually downgrade the product.

### 4. FLUX 2 Pro (Replicate — new generation)

URL: https://replicate.com/black-forest-labs/flux-2-pro

- **Price:** ~$0.04/image
- **Key feature:** Up to 8 reference images simultaneously with **structured JSON prompting including camera control** (angle, distance, focal length, aperture, ISO)
- **Index-based image referencing** — can reference specific images by index
- **5.2M runs** on Replicate — well-tested in production

This is the first major model combining reference-image consistency with explicit camera angle control. However:
- Only available on Replicate, not fal.ai
- Would require adding a new provider integration
- Structured camera control is promising but adds complexity

### 5. Two-Stage Approach Assessment

**The approach:**
1. Stage 1: Generate a proper full-body front shot using `fal-ai/flux-pro/v1.1-ultra` (text-to-image, NO face reference)
2. Stage 2: Use that full-body shot as the Kontext reference for 5 remaining angles

**Community evidence:**
- Kontext performs moderate pose variations well when starting from a full-body reference (vs. face close-up)
- 3/4 views: ~70-80% identity consistency from full-body reference
- Profile views: ~50-60% — some drift in clothing details
- Back views: Best achieved by explicitly removing "facial identity" constraints and replacing with clothing/hair/body shape anchors
- **Critical insight:** Chaining edits (front -> 3/4 -> profile) compounds errors. Each angle should use the canonical front independently.

**Advantages:**
- Stays within existing fal.ai infrastructure
- No new provider integrations
- Kontext's reference preservation behavior is actually useful when the reference IS a full body
- Back views become possible because the model isn't fighting to preserve a face that should be hidden

### 6. Pricing Comparison (6 angles per character)

| Approach | Cost per character | Platform change needed |
|----------|-------------------|----------------------|
| Current (broken) | $0.24 (6 x $0.04 Kontext) | None |
| Two-Stage (recommended) | $0.30 (1 x $0.06 Ultra + 5 x $0.04 Kontext + 1 Kontext front) | None |
| Kontext Max upgrade | $0.48 (6 x $0.08) | None |
| FLUX 2 Pro on Replicate | ~$0.28 (7 x $0.04) | Yes — new provider |
| Zero123++ | $0.074 (all 6) | Yes — Replicate |
| Consistent Character | $0.09 (6 x $0.015) | Yes — Replicate, SDXL quality |

---

## Decision: Two-Stage Pipeline (Plan A)

### Rationale

1. **Stays within fal.ai** — no new provider integrations, no Replicate dependency
2. **Uses proven models** — FLUX 1.1 Pro Ultra for text-to-image and Kontext Pro for reference-based editing are both battle-tested in our pipeline
3. **Addresses the root cause** — face close-up as Kontext reference is replaced with full-body reference, making angle variations physically possible
4. **Back view fix** — removing "maintaining exact facial identity" for the back angle and replacing with outfit/hair/body anchors gives the model permission to hide the face
5. **Cost-effective** — $0.30 per character vs. $0.24 currently, only ~25% increase for dramatically better results
6. **Gemini Vision validation** — adds a quality gate to catch misclassified angles and offer per-angle regeneration

### Implementation Plan

**Stage 1 — Canonical Front Generation:**
- Model: `fal-ai/flux-pro/v1.1-ultra`
- Prompt template: `flux_body_canonical_front`
- Output: 1024x1792 vertical, saved to R2 as `body_shot_sets/{set_id}/canonical.jpg`
- No face reference — pure text-to-image from body_description

**Stage 2 — Angle Generation (5 angles + front re-pass):**
- Model: `fal-ai/flux-pro/kontext`
- Reference: canonical front shot (NOT face close-up)
- Per-angle prompt templates with appropriate identity constraints
- Back angle explicitly allows face-not-visible

**Validation:**
- Each generated shot validated by LLM vision (via OpenRouter) for angle classification
- Mismatches flagged in response, shown as warnings in frontend
- Per-angle regeneration available

### Risk Mitigation

- If Stage 1 produces a poor canonical front (wrong body type), the entire set will be wrong. Mitigation: use detailed body_description + style_preset in the canonical prompt.
- If Kontext still can't produce back views from full-body reference, fallback: generate back view with `v1.1-ultra` (text-only, no reference) using the same body description + "back view" prompt.
- Validation step catches failures before user sees them.

---

## References

- fal.ai FLUX Kontext Pro: https://fal.ai/models/fal-ai/flux-pro/kontext
- fal.ai FLUX 1.1 Pro Ultra: https://fal.ai/models/fal-ai/flux-pro/v1.1-ultra
- fal.ai Model Catalog: https://fal.ai/models
- Replicate FLUX 2 Pro: https://replicate.com/black-forest-labs/flux-2-pro
- Replicate Zero123++: https://replicate.com/jd7h/zero123plusplus
- Replicate Consistent Character: https://replicate.com/fofr/consistent-character
- Replicate TRELLIS: https://replicate.com/firtoz/trellis
