# Avatar Pipeline v3 Reality Check

**Date:** 2026-04-13
**Auditor:** Claude (autonomous subagent)
**Scope:** Every v3 claim verified against actual code in `main` branch

---

## A1 — v3 Session Commits

### Relevant commits found:

```
78f3e0c feat: show avatar face on voice screen + upgrade ElevenLabs to v3 design endpoint
865c326 feat: show avatar face on voice screens + upgrade ElevenLabs to v3 design endpoint
ea41487 feat(avatar): Pipeline v2 — consolidate Page 1, fix body desc, body shots, 4 voices, try-on audit
b133a4b fix(avatar): OpenRouter model ID claude-sonnet-4-20250514 → claude-sonnet-4
c1ed90f fix(avatar): body description not saved — Approve & Continue saves to avatar before body shots
d2179f4 feat(avatar): per-step pipeline tracking + smart resume + stateless hydration
```

**Note:** No commit explicitly says "avatar v3". The "v3" in the ElevenLabs commits refers to the ElevenLabs Voice Design API version, not the avatar pipeline version. The pipeline work was labeled "v2" (`ea41487`). The v3 claims appear to reference an attempted but incomplete overhaul that was never fully committed.

---

## A2 — Claim-by-Claim Verification

### 1. "SetupPhase.tsx consolidates Audience + Avatar Description on one page"

**Verdict: PARTIALLY TRUE**

`SetupPhase` function exists at `AIAvatarSetup.tsx:282`. It renders BOTH Target Audience (line 453) and Avatar Description (line 548) sections in one scrollable `<div className="space-y-8">`. However:

- It is NOT a separate file `phases/SetupPhase.tsx` — it's an inline function in `AIAvatarSetup.tsx`
- The `phases/` directory does not exist at all
- The FacePhase **ALSO** has a duplicate "Give me a spark" + "Meet [Name]" flow (lines 803-960) that overlaps with SetupPhase

**Impact:** User sees SetupPhase correctly, BUT FacePhase Screen 1 ("Give me a spark") and Screen 2 ("Meet [Name]") duplicate the setup concept, creating the "3 pages before face generation" problem from the screenshots.

---

### 2. "BodyDescriptionPhase.tsx deleted"

**Verdict: FALSE — it still exists**

`BodyDescriptionPhase` function exists at `AIAvatarSetup.tsx:1479`. It is Phase 4 in the `PHASE_STEPS` array (line 27: `{ key: "body_description", label: "Body", num: 4 }`). The body description is a SEPARATE phase between Voice and Shots, not consolidated into Setup.

---

### 3. "Phase array is 5 steps: setup -> face -> voice -> shots -> preview"

**Verdict: FALSE — there are 6 steps, not 5**

`PHASE_STEPS` at `AIAvatarSetup.tsx:23-30`:
```typescript
const PHASE_STEPS = [
  { key: "setup", label: "Setup", num: 1 },
  { key: "face", label: "Face", num: 2 },
  { key: "voice", label: "Voice", num: 3 },
  { key: "body_description", label: "Body", num: 4 },
  { key: "body_shots", label: "Shots", num: 5 },
  { key: "preview", label: "Preview", num: 6 },
]
```

6 steps, not 5. `body_description` is still a standalone phase.

---

### 4. "Name auto-generates only after gender is picked"

**Verdict: PARTIALLY TRUE**

`AIAvatarSetup.tsx:377-381`:
```typescript
useEffect(() => {
  if (!audienceDesc.trim() || (nameOverridden && descriptionOverridden)) return;
  triggerNameDescGeneration(audienceDesc, gender, selectedPresets, selectedImperfections);
}, [gender]);
```

Gender change triggers regeneration. BUT: `gender` defaults to `"female"` (line 302), so the cascade fires on first audience description generation too (line 342-344), meaning the name auto-generates the moment audience description fills — before the user explicitly picks a gender. The gender is pre-selected, not a deliberate user action gate.

---

### 5. "Single LLM call returns name + description + body_description"

**Verdict: FALSE — returns name + description only, NOT body_description**

Endpoint at `avatar.py:2769`: `POST /ai/rewrite-avatar-name-and-description`

Returns: `{"name": ..., "description": ...}` (lines 2806-2808)

`body_description` is generated in a SEPARATE endpoint: `POST /ai/generate-body-description` at `avatar.py:2308`, which is called from `BodyDescriptionPhase` on mount (line 1503). There is no single call that returns all three.

Additionally, the endpoint is named `rewrite-avatar-name-and-description`, NOT `rewrite-avatar-identity` as the v3 spec claims. The frontend calls `avatarApi.aiRewriteAvatarNameAndDescription` (line 362), not `aiRewriteAvatarIdentity`.

---

### 6. "Unified active color on all toggleable elements via CSS var"

**Verdict: FALSE — no CSS variable, 3 different color schemes**

No `--accent-active` CSS variable exists anywhere in the codebase (grep returns zero results).

Current colors on toggleable elements:
- **Gender pills:** `bg-accent text-white` (line 580) — uses Tailwind `accent` which is `#8B82C0`
- **Style Presets (Setup):** `bg-purple-600 text-white` (line 602) — hardcoded `purple-600`
- **Imperfections (Setup):** `bg-orange-600 text-white` (line 623) — hardcoded `orange-600`
- **Style Presets (Face):** `border-accent bg-accent/5 ring-1 ring-accent/20` (line 870) — accent border, different pattern from Setup
- **Age range pills:** `bg-accent text-white` — accent
- **Interest chips:** `bg-accent text-white` — accent
- **Imperfection chips (Face Screen 2):** plain `border-border bg-bg` with no active color (line 921) — appends to description, no toggle state

Three competing color systems: `accent`, `purple-600`, and `orange-600`.

---

### 7. "`<AvatarIdentityPanel />` mounted in Face/Voice/Shots/Preview"

**Verdict: FALSE — component does not exist**

`AvatarIdentityPanel` does not exist in the codebase. Grep returns zero results.

What exists instead:
- `LargeAvatarHeader` inline component (line 155) — shows 200x200 image + name in a horizontal flex layout
- Used in `BodyDescriptionPhase` (line 1530) and `VoicePhase` (line 1204), but NOT in FacePhase, ShotsPhase, or PreviewPhase
- It is NOT a left-column panel (320px wide) — it's a simple header row

---

### 8. "Back navigation preserves Setup state via debounced save + rehydrate"

**Verdict: FALSE — no debounced auto-save, state lost on back-nav**

**What actually happens:**
1. SetupPhase saves to backend ONLY on "Continue" click (line 430: `avatarApi.aiSaveSetup(...)`)
2. There is NO debounced auto-save (no `setInterval`, no `setTimeout` for saving)
3. The SetupPhase `useEffect` at line 330 does audience description generation, NOT saving
4. When user goes back from FacePhase, `SetupPhase` re-mounts with all `useState` defaults:
   - `ageRange` resets to `"18-24"` (line 294)
   - `interests` resets to `[]` (line 295)
   - `audienceDesc` resets to `""` (line 296)
   - All fields empty — state lost
5. The resume logic (line 1953) reads from the server, but only fills `avatarName`, `description`, and `appearance_prompt` — NOT `ageRange`, `interests`, `gender`, `selectedPresets`, `selectedImperfections`, `audienceDesc`

**Root cause:** `SetupPhase` re-mounts from scratch with empty `useState` defaults. No server rehydration of Setup fields on back-nav. The resume logic only handles the parent component's state, not the Setup form's internal state.

---

### 9. "Creative prompts with few-shot examples"

**Verdict: PARTIALLY TRUE — some prompts have examples, key ones don't**

- `face_description_generator` (ai_prompts.py:14): 3 good examples, no bad examples
- `voice_description_generator` (ai_prompts.py:42): 1 example
- `audience_description` (ai_prompts.py:439): 1 example
- `avatar_name_and_description` (ai_prompts.py:463): 0 examples
- `gemini_avatar_description_rewrite` (ai_prompts.py:402): 0 examples
- `gemini_body_description` (ai_prompts.py:381): 0 examples

The key identity generation prompt (`avatar_name_and_description`) has ZERO few-shot examples. No prompt has "3 good + 3 bad" examples.

---

### 10. "4 voice options, not 3"

**Verdict: TRUE**

Backend generates 4 voice previews. VoicePhase frontend at line 1127 accepts an array and renders all. The backend endpoint (avatar.py) generates 4 previews via ElevenLabs Voice Design.

---

### 11. "Avatar header >= 280px desktop in voice/body/shots/preview"

**Verdict: FALSE — 200px everywhere**

`LargeAvatarHeader` component (line 169):
```typescript
style={{ width: 200, height: 200, minWidth: 200, minHeight: 200 }}
```

Hardcoded to 200x200. Not 280px. Used in VoicePhase and BodyDescriptionPhase only. Not used in FacePhase, ShotsPhase, or PreviewPhase at all.

---

### 12. "12 style presets"

**Verdict: FALSE — 6 presets in Setup, 4 in Face**

Setup phase `STYLE_PRESETS` at line 264-271:
```typescript
const STYLE_PRESETS = [
  { key: "studio", label: "Studio" },
  { key: "natural", label: "Natural" },
  { key: "cinematic", label: "Cinematic" },
  { key: "stylized", label: "Stylized" },
  { key: "street", label: "Street" },
  { key: "glamour", label: "Glamour" },
];
```
6 presets, not 12. No subtitle, no icon, not a 3x4 grid (rendered as `flex flex-wrap gap-2` pills).

Face phase `FACE_PRESETS` at line 98-127: 4 presets (Studio Pro, Natural/Real, Cinematic, Stylized/Cartoon) — different from Setup presets, rendered as 2x2 grid cards with icons.

Two separate preset systems, neither having 12 items.

---

### 13. "'Make it real' rename from 'Human Imperfections'"

**Verdict: FALSE — "Human Imperfections" still in code**

Grep results:
- `AIAvatarSetup.tsx:612`: `{/* Human Imperfections */}` (comment)
- `AIAvatarSetup.tsx:614`: `<label ...>Human Imperfections</label>` (visible UI label)
- `AIAvatarSetup.tsx:904`: `Make it real — add human imperfections` (Face Screen 2, uses hybrid label)

The Setup phase section header says "Human Imperfections" in the UI. The Face phase Screen 2 says "Make it real — add human imperfections". Inconsistent, and "Human Imperfections" is still the primary label.

---

### 14. "Body shots use locked seed + Kontext Pro reference"

**Verdict: TRUE (but the approach is fundamentally flawed)**

`avatar.py:2409`: `locked_seed = random.randint(1, 999999)` — locked across all 6 angles.
`avatar.py:2434`: `fal-ai/flux-pro/kontext` with `image_url=face_url` — uses Kontext with face reference.

Both are true. But this is the root cause of the body shots problem (see A4 below).

---

## A3 — Screenshot Cross-Check

### Screenshot 3 — "Give me a spark" page still exists

**Verified: TRUE**

`AIAvatarSetup.tsx:804-842`: FacePhase Screen 1 is "Give me a spark" — a full page with hint input, "Inspire me!" button, and example prompts. This is the first screen of the FacePhase, which means the user flow is:

1. SetupPhase (Audience + Avatar Description) → Continue
2. FacePhase Screen 1: "Give me a spark" → fill hint → Inspire me!
3. FacePhase Screen 2: "Meet [Name]" → edit description + presets + imperfections → Generate 8 faces
4. FacePhase Screen 3: Face gallery → pick face → Continue

The "Give me a spark" screen is a DUPLICATE of the SetupPhase concept. After the user already filled audience + description on Setup, they hit ANOTHER description/hint page. This is the "3 pages before face generation" problem.

---

### Screenshot 4 — "Describe your audience" on its own page

**Verified: PARTIALLY EXPLAINED**

The "Describe your audience" text appears in SetupPhase (line 456: "Who are you creating content for?"). SetupPhase IS a single page with both sections. However, because FacePhase then shows "Give me a spark" and then "Meet [Name]", the user perceives 3 separate pages. The audience section is NOT on its own page — but it FEELS like it because the flow immediately jumps to two more description-like screens.

---

### Screenshot 5 — "Meet Lily" is a third page

**Verified: TRUE**

`AIAvatarSetup.tsx:849`: FacePhase Screen 2 header says `Meet {avatarName || "your avatar"}`. This is the "Meet Lily" screen from the screenshot. It's a full page with name input, style presets (4 cards in 2x2 grid), description textarea, and "Make it real" chips (9 items).

This is the third description-like page the user sees, after SetupPhase and "Give me a spark".

---

### Screenshot 6 — Voice thumbnail tiny

**Verified: TRUE**

VoicePhase avatar header at line 1210:
```typescript
style={{ width: 200, height: 200, minWidth: 200, minHeight: 200 }}
```
200x200px, not 280px as specified. And it's rendered as a simple header row, not a left-column panel.

---

### Screenshot 7 — Body shots all near-frontal

**Verified: TRUE — see A4 for root cause**

---

## A4 — Body Shots Root Cause

### The Problem

All 6 body shots come out as near-frontal views, regardless of the angle specified in the prompt. Back views look like front views. Profile views look like slight head turns.

### Root Cause (verified in code)

**File:** `routers/avatar.py:2434`
```python
result = await fal_client.run_async(
    "fal-ai/flux-pro/kontext",
    arguments={
        "prompt": prompt,
        "image_url": face_url,  # <-- THIS IS THE FACE CLOSE-UP
        ...
    },
)
```

The `face_url` is the face close-up portrait stored in `avatar.face_ref_key`. This is a front-facing headshot.

**FLUX Kontext Pro** treats the reference image as the composition to preserve. When the reference is a front-facing face portrait:
- Kontext cannot produce a "back view" because that requires a completely different composition (no face visible)
- Kontext interprets the prompt as "modify this front-facing image slightly" rather than "generate a new view of this person from behind"
- The result is mild variations of front-facing portraits with some body filled in

**File:** `ai_prompts.py:373-375`
```
Same person as the reference image, maintaining exact facial identity, clothing, and build.
```

The `flux_body_angle` template actively fights back/profile views by insisting on "maintaining exact facial identity" — impossible when the person is facing away.

### Why `locked_seed` doesn't help

A locked seed preserves identity within the SAME generation transform. It cannot force the model to output a composition that the model doesn't produce for the given reference. All 6 shots share the same seed AND the same front-facing reference, so they all converge to similar front-facing outputs.

### What's needed

A two-stage approach:
1. **Stage 1:** Generate a proper full-body front shot (not a face close-up) using `fal-ai/flux-pro/v1.1-ultra` — fresh generation, not Kontext
2. **Stage 2:** Use the full-body front shot as Kontext reference for the other angles — Kontext can do moderate pose variations from a full-body reference
3. **Back view prompt** must drop "maintaining exact facial identity" and instead say "same outfit, same hair, same body shape — face not visible"

---

## Summary Table

| # | v3 Claim | Verdict | Details |
|---|----------|---------|---------|
| 1 | SetupPhase consolidates Audience + Avatar Description | PARTIAL | Exists but FacePhase duplicates with "Give me a spark" + "Meet [Name]" |
| 2 | BodyDescriptionPhase deleted | FALSE | Still exists as Phase 4 |
| 3 | 5-step phase array | FALSE | 6 steps |
| 4 | Name auto-generates after gender pick | PARTIAL | Gender defaults to "female", fires immediately |
| 5 | Single LLM call returns name + desc + body_desc | FALSE | Returns name + desc only, body_desc is separate endpoint |
| 6 | Unified CSS var --accent-active | FALSE | 3 different color schemes, no CSS variable |
| 7 | AvatarIdentityPanel mounted everywhere | FALSE | Component doesn't exist |
| 8 | Back nav preserves state | FALSE | No auto-save, state lost on back-nav |
| 9 | Creative prompts with few-shot examples | PARTIAL | Some have examples, key ones have zero |
| 10 | 4 voice options | TRUE | Confirmed |
| 11 | Avatar header >= 280px | FALSE | 200px everywhere |
| 12 | 12 style presets | FALSE | 6 in Setup, 4 in Face |
| 13 | "Make it real" rename | FALSE | "Human Imperfections" still in code |
| 14 | Body shots use locked seed + Kontext | TRUE | But approach is fundamentally flawed |

**Score: 2 TRUE, 4 PARTIAL, 8 FALSE out of 14 claims.**
