# Phase 0 — Blocker Diagnosis

Date: 2026-04-16
Prereqs verified:
- R2 CORS: LIVE (`access-control-allow-origin: https://www.luminacast.com`)
- B10 (VoiceCorpusEntry): DEPLOYED (17 occurrences in generate_avatar.py)

---

## 0.1 — Captions: "Failed to get upload URL"

### Classification: **(a) Endpoint missing** — Editor Starter captioning module points at a generic hosted service upload path, not our backend.

### Evidence

The captioning module at `frontend/companion-app/src/components/cast-builder/editor-starter/captioning/caption-state.ts:90-103` issues:

```typescript
const presignResponse = await fetch('/api/upload', {     // line 90
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contentType: 'audio/wav', size: audio.byteLength }),
});
if (!presignResponse.ok) {
    const errorData = await presignResponse.json();
    throw new Error(errorData.error || 'Failed to get upload URL');  // line 103
}
```

After receiving a presigned URL, it then calls `/api/captions` at line 143:

```typescript
const res = await fetch(`/api/captions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fileKey: presignData.fileKey }),
});
```

**Neither `/api/upload` nor `/api/captions` exists in the backend.** Grepping `backend/orchestrator/routers/` and `backend/orchestrator/main.py` confirms:

- No router is mounted with a generic `/api/upload` endpoint (`main.py:163-186` — no upload router)
- No `/api/captions` route is mounted either
- The backend DOES have `POST /api/casts/{cast_id}/generate-captions` (`casts.py:1905`) and `POST /api/casts/{cast_id}/editor-generate-captions` (`casts.py:2002`), but these take audio URLs directly, not file keys from a presigned upload

The captioning module also uses a secondary upload helper at `editor-starter/utils/use-uploader.ts:23` which also calls `/api/upload`.

**Root cause**: The Editor Starter captioning module was ported from the upstream Editor Starter which assumes a hosted backend providing `/api/upload` (presigned S3 URL) and `/api/captions` (Whisper transcription). Our backend has neither of these generic endpoints. The existing `editor-generate-captions` endpoint at `casts.py:2002` takes audio URLs directly and returns Remotion-compatible captions, but the Editor Starter captioning UI is wired to the generic upload→transcribe flow.

**Fix path**: Implement `POST /api/upload` (returns presigned R2 PUT URL) and `POST /api/captions` (accepts fileKey, transcribes via WhisperX, returns captions in Editor Starter format), OR rewire the captioning module to use the existing `editor-generate-captions` endpoint which accepts audio URLs directly.

---

## 0.2 — Editor audio silent on playback

### Classification: **(b) src is set correctly** — audio items ARE created in the timeline with valid URLs, but playback may fail due to Remotion Player configuration.

### Evidence

The bridge function `castToEditorStarterTimeline()` at `frontend/companion-app/src/lib/editorStarterMapping.ts:136-280` correctly creates audio items:

- Audio source resolution at line 137-139:
  ```typescript
  const audioSrc =
      variant.audio_url ||
      (variant.audio_key ? cdnUrl(variant.audio_key) : "");
  ```
- AudioItem creation at lines 257-280 with proper `from`, `durationInFrames`, `assetId` linking to an AudioAsset with `remoteUrl: audioSrc`
- AudioAsset at lines 245-254 with `type: "audio"`, `remoteUrl: audioSrc`
- Backend returns `audio_url` at `casts.py:178`: `f"https://media.luminacast.com/{v.audio_key}"`

The audio layer component at `editor-starter/items/audio/audio-layer.tsx:1-64` uses Remotion's `<Audio>` and `<Html5Audio>` components with the asset's `remoteUrl` as `src`.

**Potential issue**: The AudioLayer (`audio-layer.tsx:43-64`) renders `<Audio src={...}>` inside a Remotion `<Sequence>`. Playback works in the Player only if `numberOfSharedAudioTags` is configured properly on the Remotion Player component. If the Player is rendered without `numberOfSharedAudioTags` or with `numberOfSharedAudioTags: 0`, audio will be silent.

Additionally, R2 CORS is now live (verified), so cross-origin audio fetch should work. The `<Audio>` component from `@remotion/media` uses `crossOrigin="anonymous"` by default.

**Diagnosis**: Audio items exist with correct sources. The silence is likely a Remotion Player configuration issue (`numberOfSharedAudioTags` not set) or a Chrome autoplay policy issue in the preview context. Needs browser DevTools verification — check if the `<audio>` element in DOM has a valid `src` and `readyState > 0`.

---

## 0.3 — Mine sub-tab empty in editor

### Classification: **Structural mismatch** — there is no "Mine" sub-tab. The media panel uses a flat tab structure without the spec's required Mine/Stock/Generated sub-tabs.

### Evidence

`LuminacastMediaPanel.tsx:193-198` defines flat tabs:
```typescript
const tabs = [
    { key: "my-videos", label: "My Videos", icon: Upload, needsSearch: false },
    { key: "stock-photos", label: "Photos", icon: Image, needsSearch: true },
    { key: "stock-videos", label: "Videos", icon: Film, needsSearch: true },
    { key: "music", label: "Music", icon: Music, needsSearch: false },
];
```

This is a flat 4-tab structure: `My Videos | Photos | Videos | Music`. The spec (decision 10) requires:
```
Videos  |  Photos  |  Music
 └─ [Mine] [Stock] [Generated]
```

**"My Videos" tab does fetch user data** — `LuminacastMediaPanel.tsx:52-58` calls `userVideosApi.list()` which hits `GET /api/user-videos` (`user_videos.py:63`). Backend data exists: 7 user_video_assets for the admin user in the database.

**Missing tabs**: No "Generated Photos", "Generated Videos", or "Generated" sub-tab of any kind. Backend endpoints exist (`generated_photos.py:94` — `GET /api/generated-photos/generated`, `generated_videos.py:122` — `GET /api/generated-videos/generated`), but the media panel doesn't call them.

**Why "My Videos" might appear empty**: If the API returns videos but they lack the fields the media panel expects (`url`, `src`, `thumb`), they'd render as blank cards. The mapping at line 54 sets `thumb: v.url` — if `v.url` is undefined (backend returns `r2_key` not `url`), thumbnails would be blank.

---

## 0.4 — Avatar Clone Upload pipeline failing

### Classification: **Two distinct failure modes**: (1) Voice cloning stage stuck in retry loop, (2) Image generation / video rendering timeout.

### Evidence

**DB state** (admin user avatars, last 10):

| Avatar ID | Type | Status | Phase | Progress Step |
|---|---|---|---|---|
| avt_82568f5b8947 | CLONE | FAILED | failed | Video rendering failed |
| avt_e644ea9602d9 | CLONE | FAILED | failed | Video rendering failed |
| avt_3d4e1eba72e9 | CLONE | FAILED | image | Generation timed out |
| avt_110c2b7babba | CLONE | FAILED | image | Generation timed out |
| avt_7c527ca42d36 | CLONE | FAILED | failed | Video rendering failed |
| avt_ddfc63dbb2c9 | CLONE | FAILED | image | Generation timed out |
| avt_cf54ef0b1037 | CLONE | FAILED | image | Generation timed out |
| avt_7b97e81338fb | DIGITAL | FAILED | image | Generation timed out |
| avt_6c9fb18d70c6 | CLONE | FAILED | image | Generation timed out |
| avt_acb8fbff818c | CLONE | FAILED | image | Generation timed out |

All 10 recent avatars FAILED. Zero reached READY.

**Failure mode 1 — Voice clone retry loop** (`generate_avatar.py:852-861`):

Celery logs show `avt_82568f5b8947` cloned voice successfully via self-hosted Fish Speech (voice_id = `voices/refs/be5062ced4ec/reference.wav`), but the avatar's `voice_id` column stayed NULL. The pipeline then enters a 12×5s poll loop at `generate_avatar.py:855-861` waiting for `avatar.voice_id` to be set — but the voice_id was never persisted. After the poll loop exits, `_mark_failed` is called at line 867.

Root cause: The `clone_voice()` call in the parallel voice pipeline succeeds but the voice_id is NOT being written back to the avatar record before the main pipeline reads it. The `voice_sample_key` is empty too — suggesting the upload path didn't save the voice sample to R2 before the pipeline started.

**Failure mode 2 — Image generation timeout** (5 of 10 avatars):

Multiple CLONE avatars stuck at `active_phase = 'image'` with "Generation timed out." Sentry shows `FalClientHTTPError` (6x, LUMINACAST-ORCHESTRATOR-4V) and `HTTPStatusError: Server error '500 Internal Server Error' for url 'http://194.247.183.12:7860/api/qw...` (6x, LUMINACAST-ORCHESTRATOR-4W) — the GPU server's FLUX / QwenImage endpoints are failing.

GPU worker Sentry shows `RuntimeError: Failed to import diffusers.pipelines.qwenimage.pipeline_qwenimage_edit` (LUMINACAST-GPU-WORKER-V, 6x) — the QwenImage Edit pipeline fails to import, likely missing a dependency or version mismatch.

**Failure mode 3 — InfiniteTalk** (implied by Sentry):

`HTTPException: InfiniteTalk weights not downloaded yet` (LUMINACAST-GPU-WORKER-T, 1x). This blocks the test video generation step.

**Summary**: The clone upload pipeline is broken at multiple stages:
1. Voice cloning succeeds but voice_id doesn't propagate to the avatar record
2. GPU image generation (FLUX/QwenImage) is failing with import errors
3. InfiniteTalk weights may not be loaded on GPU server

---

## 0.5 — Safe zones identical per platform

### Classification: **Three separate definitions that are genuinely distinct** — but only have top/bottom horizontal bands, missing the platform-specific side column overlays.

### Evidence

`frontend/companion-app/src/lib/safeZones.ts:41-66` defines three platform configs:

| Platform | Top Zone | Bottom Zone | Side Columns |
|---|---|---|---|
| TikTok | 0-15% | 75-100% (25%) | **NONE** |
| Instagram Reels | 0-10% | 80-100% (20%) | **NONE** |
| YouTube Shorts | 0-12% | 78-100% (22%) | **NONE** |

The three definitions ARE numerically distinct (different top heights: 15/10/12%, different bottom starts: 75/80/78%). However, all three only define horizontal top/bottom bands. None define the **right-column icon stack** (like/comment/share buttons at ~92% horizontal) that is the most distinctive per-platform element.

The spec requires:
- **TikTok**: right column icon stack at ~92% horizontal × 40-85% vertical
- **Reels**: right column icons (different position)
- **Shorts**: right column minimal, bottom subscribe/like/comment bar wider

Current safe zones only cover top status bar and bottom caption/nav areas. The side column overlays (which are the most platform-distinctive elements) are completely missing. Visually, all three platforms look nearly identical — just slightly different height horizontal bands.

The overlay renderer at `SafeZoneOverlay.tsx` correctly renders per-zone rectangles, and the toggle at `safe-zone-toggle.tsx` correctly switches between platforms. The data is just incomplete.

---

## 0.6 — Captions generating as 1/6

### Classification: **(b) Progress counter counts per-variant, not per-block.** 3 blocks × 2 variants each = 6 total. Backend correctly shows "Audio 1/6" because it counts variant-level TTS jobs.

### Evidence

`backend/orchestrator/tasks/generate_cast.py:521`:
```python
total_variants = sum(len(getattr(b, 'variants', [])) for b in blocks)
```

Progress step at `generate_cast.py:616`:
```python
cast.progress_step = f"Audio {completed}/{total_variants}"
```

Database confirms 3-block casts have 6 variants:
```sql
SELECT b.id, count(v.id) FROM blocks b
LEFT JOIN variants v ON v.block_id = b.id
WHERE b.cast_id = 'cst_32d2d5f3c1a9' GROUP BY b.id;
-- blk_028fd3fc18c9 | 2
-- blk_1ae5af280128 | 2
-- blk_e4da95c2fb9d | 2
```

Each block has 2 variants (likely variant A and B, or a regenerated variant). The progress message is technically correct — it IS generating 6 TTS audio files — but confusing to the user who thinks in terms of blocks, not variants.

**Frontend display**: `AudioGeneratingPhase.tsx:26` shows the raw `progress_step` from the backend:
```typescript
setStep(cast.progress_step || "Generating audio...");
```

No frontend transformation is applied. The message "Audio 1/6" is displayed verbatim.

**Fix**: Either show `"Block 1/3 (variant 1/2)"` to explain the breakdown, or aggregate to block-level progress `"Block 1/3"` since users don't understand variants.

---

## 0.7 — Word count in Script Editor wrong

### Classification: **Word count includes ALL text content** — `[gesture:]` markers, stage directions, and any non-TTS text in `script_text` are counted because the word count function does plain whitespace splitting with no filtering.

### Evidence

`frontend/companion-app/src/components/cast-builder/ScriptPhase.tsx:92-93`:
```typescript
function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}
```

This is a naive whitespace split. It counts EVERYTHING in the text, including:
- `[gesture:wave]` markers (counts as 1 word: `[gesture:wave]`)
- `[gesture: pointing forward]` (counts as 3 words)
- Any metadata that may be in `variant.script_text`

Used at `ScriptPhase.tsx:349` for total:
```typescript
const totalWords = blocks.reduce((sum, b) => {
    const variant = b.variants?.[0];
    return sum + (variant?.script_text ? wordCount(variant.script_text) : 0);
}, 0);
```

And at `ScriptPhase.tsx:401` per-block:
```typescript
const wc = wordCount(text);
```

The word count is used for both display (`{wc} words` at line 444) and duration estimate (`estimateDuration` at line 96-98 divides by 2.5 words/sec). So inflated word count → inflated duration estimate.

**Fix**: Strip `[gesture:...]` markers before counting:
```typescript
function wordCount(text: string): number {
  const cleaned = text.replace(/\[gesture:[^\]]*\]/g, '');
  return cleaned.trim().split(/\s+/).filter(Boolean).length;
}
```

---

## Summary

| Bug | Classification | Severity |
|-----|---------------|----------|
| 0.1 Captions upload | (a) Endpoint missing — `/api/upload` and `/api/captions` don't exist in backend | HIGH |
| 0.2 Audio silent | (b) Audio items created with correct src — likely Remotion Player config (`numberOfSharedAudioTags`) or autoplay policy | MEDIUM |
| 0.3 Mine sub-tab empty | Structural mismatch — no Mine/Stock/Generated sub-tab structure; flat tabs only, Generated tabs missing entirely | MEDIUM |
| 0.4 Avatar clone failing | Multi-stage failure: voice_id not propagated, GPU image gen errors (QwenImage import failure), InfiniteTalk weights not loaded | CRITICAL |
| 0.5 Safe zones identical | Three distinct definitions exist but are incomplete — missing right-column side overlays that differentiate platforms | LOW |
| 0.6 Captions 1/6 | Progress counts per-variant (2 variants per block × 3 blocks = 6), not per-block — confusing but technically correct | LOW |
| 0.7 Word count wrong | Naive `split(/\s+/)` includes `[gesture:]` markers in count — inflates word count and duration estimate | LOW |
