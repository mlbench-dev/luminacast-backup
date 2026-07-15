# Phase 2.0 — Pre-flight Evidence

**Date:** 2026-04-15
**Source:** `/opt/luminacast-omni/external/remotion-editor-starter/` (VPS) + local codebase

---

## 2.0.1 — Editor Starter Capabilities

### 1. Drag/Resize/Position on Canvas — CONFIRMED

**Drag-to-move:**
- File: `src/editor/selection-border/selection-outline.tsx` (lines 137–361)
- Function `startDragging()` captures `pointerdown`, tracks `pointermove` offsets, calls `changeItem()` to update `left`/`top` on every selected item
- Supports multi-select drag, shift-axis-lock, and canvas snapping

**Resize handles:**
- File: `src/editor/selection-border/resize-handle.tsx` (lines 92–496)
- Eight handles (4 corners + 4 edges) rendered per selection
- `ResizeType = 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | 'top' | 'right' | 'bottom' | 'left'`
- `onPointerDown` handler computes new `width`, `height`, `left`, `top` based on drag delta, with aspect-ratio lock and canvas snapping

**Canvas overlay:**
- File: `src/editor/selection-border/sorted-outlines.tsx`
- Renders `SelectionOutline` for every visible item as an overlay on the Remotion player canvas

### 2. Z-Index / Layer Ordering — CONFIRMED (track-based)

**Rendering order:**
- File: `src/editor/canvas/layers.tsx` (line 20)
- `tracks.slice().reverse().map(...)` — tracks rendered in reverse order, so track index 0 = front-most layer

**Bring-to-front/Send-to-back:**
- File: `src/editor/state/actions/bring-item-to-front-or-back.ts` (lines 12–63)
- `bringToFrontOrBack()` removes item from its current track and re-inserts it at front or back of the track list via `findSpaceForItem({startPosition: {type: position}})`

**Context menu:**
- File: `src/editor/timeline/timeline-item/timeline-item-context-menu.tsx` (lines 151–168)
- Exposes "Bring to front" and "Send to back" menu items
- Feature flags: `FEATURE_BRING_TO_FRONT = true`, `FEATURE_SEND_TO_BACK = true` in `src/editor/flags.ts` (lines 92–93)

**Important:** Items do NOT have a CSS `zIndex` field. Layer order is determined entirely by track position in `state.undoableState.tracks[]`. For Phase 2.9 (layer panel), we'll need to expose track reordering.

### 3. Per-Element Transform Properties — CONFIRMED

**File:** `src/editor/items/shared.ts` (lines 1–26)

| Property | Field Name | Type | Notes |
|----------|-----------|------|-------|
| Position X | `left` | number | Pixels from canvas left |
| Position Y | `top` | number | Pixels from canvas top |
| Width | `width` | number | Pixels |
| Height | `height` | number | Pixels |
| Opacity | `opacity` | number | 0–1 |
| Rotation | `rotation` | number | Degrees (via `CanHaveRotation` mixin) |
| Border radius | `borderRadius` | number | Via `CanHaveBorderRadius` mixin |
| Crop | `cropLeft`, `cropTop`, `cropRight`, `cropBottom` | number | Via `CanHaveCrop` mixin |

**No `scale` property.** Scaling is achieved by changing `width`/`height` directly.

**CSS application:** `src/editor/items/croppable-layer.ts` (lines 54–70) — `position: absolute`, `left`, `top`, `transform: rotate(Ndeg)`, `opacity`, `width`, `height`, `borderRadius`.

### 4. CaptionsItem Schema — CONFIRMED (two-layer structure)

**CaptionsItem:**
- File: `src/editor/items/captions/captions-item-type.ts` (lines 4–24)
- Fields: `type: 'captions'`, `assetId` (links to CaptionAsset), `fontFamily`, `fontStyle`, `lineHeight`, `letterSpacing`, `fontSize`, `align`, `color`, `highlightColor`, `strokeWidth`, `strokeColor`, `direction`, `pageDurationInMilliseconds`, `captionStartInSeconds`, `maxLines`, `fadeInDurationInSeconds`, `fadeOutDurationInSeconds`
- Inherits `BaseItem` + `CanHaveRotation` fields

**CaptionAsset (where tokens live):**
- File: `src/editor/assets/assets.ts` (lines 70–73)
- `type: 'caption'`, `captions: Caption[]` (from `@remotion/captions` v4.0.433)

**Caption token structure:**
- Per `edit-caption-line.tsx` (lines 28–37): `{text: string, startMs: number, endMs: number}`
- TikTok-style page grouping via `createTikTokStyleCaptions()` with `combineTokensWithinMilliseconds`
- Pages have `tokens[]` array with `{fromMs, toMs, text}` entries

### 5. Item Creation API — CONFIRMED

**Core function:** `addItem()` in `src/editor/state/actions/add-item.ts` (lines 64–103)
- Takes `{state, item, select, position}` → returns new `EditorState` with item added to `state.undoableState.items` and placed on a track

**State update pattern:**
- All mutations go through `setState({update: (state) => newState, commitToUndoStack: bool})` from `useWriteContext()`

**Item update:** `changeItem()` in `src/editor/state/actions/change-item.ts` (lines 4–30)

**Factory functions:**
- `makeVideoItem()` → `src/editor/items/video/make-video-item.ts`
- `makeImageItem()` → `src/editor/items/image/make-image-item.ts`
- `makeAudioItem()` → `src/editor/items/audio/make-audio-item.ts`
- `makeGifItem()` → `src/editor/items/gif/make-gif-item.ts`
- `createTextItem()` → `src/editor/items/text/create-text-item.ts`
- Captions and Solid items created inline

**Full flow:** `addAsset()` → `makeItem()` (type detection, calls factory) → `setState()` calling `addItem()` + `addAssetToState()`

---

## 2.0.2 — Backend Endpoint Inventory

### Existing Endpoints
| Endpoint | Status | Location |
|----------|--------|----------|
| `POST /api/casts/{id}/generate-tts` | EXISTS | `casts.py:1488` — generates TTS for ALL active blocks |
| `POST /api/casts/{id}/generate-captions` | EXISTS | `casts.py:1594` — Whisper on GPU, word-level timestamps |
| `POST /api/casts/{id}/generate-videos` | EXISTS | `casts.py:1538` — submits InfiniteTalk/render jobs |

### Endpoints To Create
| Endpoint | Phase | Notes |
|----------|-------|-------|
| `POST /api/casts/{id}/blocks/{id}/regenerate-audio` | 2.1 | Per-block TTS regen, creates new Variant |
| `GET /api/casts/{id}/blocks/{id}/variants` | 2.1 | List all variants for variant picker |
| `PATCH /api/casts/{id}/blocks/{id}/select-variant` | 2.1 | Switch active variant |
| `POST /api/casts/{id}/duplicate-as` | 2.4 | Cross-format duplication |
| `GET /api/casts/{id}/siblings` | 2.4 | Sibling cast lookup |

---

## 2.0.3 — Existing Variant Model Inventory

**File:** `backend/orchestrator/models/variant.py`

### All 27 Columns
| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `id` | String | PK, prefixed | Primary key |
| `block_id` | String | FK to blocks, indexed | Relationship |
| `status` | Enum(VariantStatus) | PENDING | PENDING/GENERATING/READY/FAILED |
| `variant_label` | String | "" | A/B label |
| `variant_style` | String | "" | Style tag |
| `script_text` | Text | "" | Script content |
| `motion_prompt` | Text | "" | Motion description |
| `estimated_duration_seconds` | Float | None | Estimate before TTS |
| `tts_r2_key` | String | "" | TTS audio R2 key |
| `tts_duration_seconds` | Float | None | TTS audio duration |
| `audio_key` | String | None | Final audio R2 key |
| `video_key` | String | None | Rendered video R2 key |
| `video_r2_key` | String | None | Legacy video key |
| `video_r2_url` | String | None | Legacy video URL |
| `final_video_key` | String(500) | None | Composited final video |
| `caption_words` | JSON | None | Per-word Whisper data |
| `caption_segments` | JSON | None | Whisper segments |
| `duration_seconds` | Float | None | Final duration |
| `weight` | Float | 1.0 | A/B weight |
| `times_played` | Integer | 0 | Play count |
| `purchases_during` | Integer | 0 | Purchase count |
| `performance_score` | Float | None | Computed score |
| `generation_error` | String | None | Error message |
| `composition_warnings` | JSON | None | Warnings list |
| `retry_count` | Integer | 0 | Retry count |
| `runpod_job_id` | String | None, indexed | RunPod job tracker |
| `updated_at` | DateTime | None | Last update |

### Key Finding: `is_active` Does NOT Exist on Variant
- `is_active` exists on **Block** (block.py line 61), not on Variant
- Need to add `is_active` to Variant model for Phase 2.1
- `created_at` is referenced in code for ordering but NOT defined on the model — need to add it too

### Block Model (relevant fields)
- `is_active` (Boolean, default=True, indexed) — for block active state
- `render_mode` (String(20), default="avatar_full")
- `deleted_at` (DateTime, nullable) — soft delete
- `variants` relationship (cascade="all, delete-orphan")

### TTS Pipeline (reuse path for Phase 2.1)

**Service:** `FishAudioService` in `backend/orchestrator/services/fish_audio.py`
- Singleton: `get_fish_audio_service()`
- Method: `async def generate_tts(self, text: str, voice_id: str) -> dict`
- Returns: `{"audio_key": str, "duration_seconds": float, "tmp_path": str}`
- Three backends tried in order: (1) GPU fish_speech_tts, (2) RunPod Fish Speech, (3) Fish Audio API
- All upload MP3 to R2 and return same dict

**Current usage in generate_cast.py (line 566):**
```python
tts_result = await fish.generate_tts(text=script_text, voice_id=avatar_voice_id)
# Then sets: variant.audio_key, variant.tts_r2_key, variant.tts_duration_seconds, variant.duration_seconds
```

**Voice ID source:** `cast.avatar.voice_id` (requires Cast → Avatar join)

**Duration guard:** Rejects audio > 18.0 seconds

**Post-TTS:** Captions auto-generated via Whisper (non-fatal)

---

## Summary: Ready for Phase 2.1

The data model supports multiple variants per block with full Whisper alignment. To implement per-block audio regeneration:

1. Add `is_active` Boolean to Variant + `created_at` timestamp
2. Backfill: most recent variant per block gets `is_active=True`
3. Create 3 new endpoints (regenerate, list, select)
4. Reuse `FishAudioService.generate_tts()` for TTS calls
5. Voice ID from `cast.avatar.voice_id`
