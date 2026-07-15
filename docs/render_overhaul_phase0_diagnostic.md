# Phase 0 — Render Quality Diagnostic Report

Render under analysis: `rnd_b90b9d1421ea` / cast `cst_d2dd91985028`.
Date: 2026-06-11. No code changed in this phase (diagnostics only).

Environment note: the repo is a shallow sparse worktree. The spec's named file
`cast_ffmpeg_composer.py` exists but is **not** where the reverse/ping-pong/freeze
timing logic lives — that logic was refactored out into
`services/block_extension.py`, `services/block_normalize.py`, and the
timeline-level healer in `tasks/cast_render.py`. All file:line references below were
read from the materialized tree at HEAD `7471cbe`.

---

## 0.1 Raw lip-sync check

All five raw baked clips downloaded successfully from the public CDN
(`https://media.luminacast.com/renders/rnd_b90b9d1421ea/baked_blocks/v1_{block}.mp4`).

**TTS WAV caveat:** the TTS files at
`https://media.luminacast.com/lipsync_prep/rnd_b90b9d1421ea/{block}-<hash>.wav`
require the per-block hash, which is only in the worker logs (HTTP 404 without it).
I did **not** SSH to production to retrieve them (instructions say to do so only if
the check is otherwise impossible). It is not necessary here: each raw clip embeds
its own AAC 48 kHz stereo audio stream, and for a WaveSpeed InfiniteTalk bake **the
embedded audio IS the lipsync TTS track** (A1 voice is consumed by the provider, per
`cast_ffmpeg_composer.py` lines 1–11 and 300–303). The embedded-audio duration is
therefore the authoritative TTS duration for the lip-sync delta and is used as such
below. `ffprobe` command used per spec:
`ffprobe -v error -show_entries stream=codec_type,r_frame_rate,nb_frames,duration -show_entries format=duration -of json <clip>`.

**Visual inspection:** frame extraction works in this environment. A mid-clip frame
of `blk_4e9daa13fcbb` decoded to a normal 2.1 MB still (a face, not black). I could
not do a frame-by-frame human lip-sync judgement, so the sync verdict rests on the
duration analysis + black/freeze passes below (this is the documented limitation).

| Block ID | Container dur | Video stream dur | FPS | Frames | TTS dur (embedded audio) | Delta % | In sync (raw)? |
|---|---|---|---|---|---|---|---|
| blk_4e9daa13fcbb | 4.500 s | 4.500 s | 30 | 135 | 4.467 s | +0.73% | YES |
| blk_972fc59c103a | 4.633 s | 4.633 s | 30 | 139 | 4.633 s | +0.01% | YES |
| blk_0bd1cc27c4d0 | 10.867 s | 10.867 s | 30 | 326 | 10.867 s | −0.00% | YES |
| blk_6496c88e27ad | 4.733 s | 4.733 s | 30 | 142 | 4.700 s | +0.70% | YES |
| blk_afc2418b63b2 | 6.400 s | 6.400 s | 30 | 192 | 6.367 s | +0.52% | YES |

Supporting checks (all five clips):
- **blackdetect** (`d=0.5:pix_th=0.10`): 0 black segments on every clip.
- **freezedetect** (`n=0.001:d=2.0`): 0 frozen segments on every clip.
- Every video stream duration matches its embedded audio within ≤ 0.033 s
  (≤ 1 frame at 30 fps).
- Every delta is comfortably inside the Phase 2 speaking tolerance (−2% / +5%).

**Decision matrix verdict: composer-caused.**
Raw provider clips are internally A/V-consistent, contain live (non-black,
non-frozen) motion, and are correctly lengthed against their own audio. Any lip
desync seen in the final composed render of `rnd_b90b9d1421ea` is introduced by the
composition / extension / remux layer, not by WaveSpeed. No provider escalation is
warranted. Phases 1–2 are the correct fix path.

---

## 0.2 Composer timing audit

The "composer" is three cooperating modules, not one file:

- `services/cast_ffmpeg_composer.py` — timeline→FFmpeg translation (concat,
  overlays, captions, carousel). Does **not** stretch/reverse bonded clips.
- `services/block_normalize.py` — per-block conform (scale/fps/trim) + the legacy
  ping-pong extension filter builder.
- `services/block_extension.py` — per-block undershoot healer (micro-pad /
  short-tail-reverse / I2V re-bake).
- `tasks/cast_render.py` — orchestration + a timeline-level post-compose healer
  (`_post_compose_audio_remux` / `_extend_compose_video_to_expected`).

### Reverse / ping-pong / freeze sites

| File | Line | Pattern | Snippet |
|---|---|---|---|
| services/block_extension.py | 193–268 | `reverse` (short-tail-reverse) | `_short_tail_reverse()` — `...setpts=PTS-STARTPTS,reverse,trim=duration={undershoot_s}...` then `concat` forward+reversed tail (line 238) |
| services/block_extension.py | 480–498 | ping-pong dispatch | `strategy = "short-pingpong"` branch routes 0.25 s–0.6 s undershoots into `_short_tail_reverse` |
| services/block_extension.py | 524–549 | reverse fallback | re-bake failure falls through to a capped `_short_tail_reverse` |
| services/block_extension.py | 671–688 | reverse fallback | `_rebake_loop` residual > 0.25 s → `_short_tail_reverse` |
| services/block_extension.py | 143–190 | freeze-pad (`tpad=clone`) | `_micro_pad()` — `tpad=stop_mode=clone:stop_duration=...` (line 163) holds last frame for ≤ 0.25 s undershoots |
| services/block_normalize.py | 54–141 | ping-pong builder | `build_pingpong_video_filter()` — `split` → even forward / odd `reverse` (line 117) → `concat` → trim/`setpts` stretch |
| services/block_normalize.py | 308–323 | ping-pong call site | `if extend_to_slot:` engages `build_pingpong_video_filter`. **Currently dormant** — the only in-tree caller (`cast_render._normalize_for_canvas`, line 1398) passes `extend_to_slot=False`. |
| tasks/cast_render.py | 895–918 | freeze-pad (`tpad=clone`) | `_hold_last_frame()` → `_micro_pad(...)` timeline-level hold-last-frame |
| tasks/cast_render.py | 920–976 | hold-frame / re-bake ladder | `_extend_compose_video_to_expected()` — micro-pad → hold-frame → I2V re-bake → hold-frame fallback (explicitly **never** reverses at timeline level) |

Notes:
- The legacy ping-pong path in `block_normalize.py` is reachable only via
  `extend_to_slot=True`, which no current caller passes; it is dead-but-present and
  Phase 1 should **delete** it (spec § 1.2.1 — delete, do not flag-disable).
- `block_extension._short_tail_reverse` IS live on the speaking/motion bake path
  (called from `_normalize_for_canvas` → `extend_block_to_slot`) for undershoots in
  the 0.25 s–0.6 s band and as the re-bake fallback. This is the source of the
  "walks backward / ping-pong" defect #1. Phase 1 deletes it.
- `_micro_pad`'s `tpad=stop_mode=clone` is the freeze-last-frame mechanism (defect
  #4 risk). Spec § 1.2.2 says delete freeze-pad branches; this is the one.

### setpts / atempo / forced fps sites

| File | Line | Pattern | Snippet |
|---|---|---|---|
| services/block_normalize.py | 135–138 | `setpts` **video stretch** | `[vcat]setpts=PTS*{stretch:.6f}` — stretches concatenated ping-pong to land on `target_dur` when the loop cap is hit. This is video retiming-to-slot — spec § 1.2.4 says remove. (Only reachable via the dormant `extend_to_slot=True`.) |
| services/block_normalize.py | 303 | forced fps | `[0:v]fps={target_fps},scale=...` — re-stamps every bake to canvas fps. Duration-preserving (CFR conversion), not a container re-stamp. |
| services/block_extension.py | 233, 238–239 | `setpts=PTS-STARTPTS` | PTS-rebasing around the reverse concat — harmless rebasing, but lives inside the reverse path being deleted. |
| services/block_extension.py | 161, 365–371 | forced fps | `fps={target_fps}` in micro-pad and concat-with-pad normalisation. Duration-preserving. |
| services/cast_ffmpeg_composer.py | 1138, 1142 | `setpts=PTS-STARTPTS` | child-clip trim rebasing in `build_clip_filter_complex` (sub-cast trimming) — rebasing only, not a stretch. Not in scope. |
| (search) atempo | — | none | **No `atempo` anywhere in the render path.** Audio is never time-stretched. Good — speaking sync is safe from pitch-shift. |
| services/block_normalize.py | 343–387 | `apad=whole_dur` | audio silence-pad to slot — extends audio with silence, never re-pitches. |

### Audio–video sync mechanics

Two distinct audio paths exist, and the second is the likely desync culprit:

1. **Embedded clip audio (primary).** In `cast_ffmpeg_composer.translate_timeline_to_ffmpeg`,
   each bonded baked clip enters as one `-i` with both video and audio
   (`FFmpegInput(has_video=True, has_audio=True)`, line 350). Each segment's audio
   passes through `anull` (line 507/515) and is concatenated **alongside** its own
   video in a single `concat=n=N:v=1:a=1` (line 521). Because each clip's audio and
   video share the same input and are concatenated together frame-aligned, the
   embedded TTS stays locked to the lips **as long as the per-block video duration is
   not altered after the bake**. The 0.1 finding shows the raw clips arrive aligned,
   so this primary path preserves sync.

2. **Defensive post-compose remux (`_post_compose_audio_remux`, cast_render.py:980).**
   After compose, this rebuilds the **entire audio mix from scratch** from the
   timeline's audio-track elements, delaying each element with `adelay` to its
   **absolute slot start** (`expected_duration` / per-element `s`). It then re-muxes
   that freshly-built audio over the composed video. The audio anchor here is the
   **timeline's nominal slot start (`s`)**, NOT the baked clip's actual presentation
   timestamp. If any upstream step changed a block's effective video duration
   (ping-pong stretch, short-tail-reverse adding frames, hold-frame padding, or a
   timeline-level extend), the composed video's block boundaries no longer fall on
   the nominal `s` offsets the remux's `adelay` assumes → **audio drifts relative to
   video = the observed lip desync (defect #2).** The remux's video stream is passed
   through untouched (`[0:v]`), so the desync is purely the audio re-anchoring
   fighting a video timeline whose block lengths were altered by the
   extension/padding paths.

**Why fixing Phases 1–2 resolves desync:** once motion blocks are *trimmed* to slot
(never stretched/reversed/padded) and speaking blocks are accepted only within
tolerance (never padded), each block's composed video duration equals its nominal
slot duration. The remux's `adelay`-to-`s` anchoring then lines up exactly, and the
embedded-audio concat path was already aligned. No retiming means no drift.

---

## 0.3 Product overlay resolution trace

**Resolution branch:** `services/video_compositor.py:456` —
`async def ensure_product_cover_on_r2(product, db=None)`.

**Asset selection logic (prose):** the function resolves a product's overlay image
through a 5-step ladder:
1. `product.cover_image_key` if already set (line 476).
2. **TrendingProduct lookup** by `product.tiktok_product_id` → downloads
   `TrendingProduct.cover_image_url` and caches it as the cover (lines 480–519).
3. **First ProductAsset** with `media_type='image'`, **ordered by
   `created_at.asc()`** — i.e. oldest-imported wins (lines 523–539).
4. Scan `product.media_keys` JSON for any image-extension key (lines 541–548).
5. Return `None` → overlay skipped (lines 550–555).

The block references the product by **`product_id` only** (timeline image elements
carry `metadata.product_id`; see `twick_compositor_adapter.py:139` and the
`product_overlays` collection at line 153). There is **no asset-id and no
cover-flag** carried on the overlay element — the renderer re-resolves "which image"
purely from `product_id` at compose time via `ensure_product_cover_on_r2`. The block
never pins a specific `ProductAsset`.

**Where the "stock shampoo" came from (exact branch):** Step 2
(`video_compositor.py:480–519`). When `product.cover_image_key` is empty, the code
proxies `TrendingProduct.cover_image_url` — the **generic trending-feed cover image
for the TikTok product id**, which for the failing render was a generic stock
shampoo bottle rather than the user's imported product photo. This is a silent
substitution of an unrelated image. Step 3 is also wrong-by-ordering: it picks the
oldest `ProductAsset` by `created_at`, ignoring the `position` column whose own
docstring (`models/product_asset.py:31–32`) declares `0 = cover, 1+ = additional
media`. So even when real imported assets exist, the wrong one can be chosen.

**"Import ALL images to ProductAssets" impact:** the git history is shallow in this
worktree (only HEAD visible), but the `position` column was added by migration
`pa01_add_product_asset_position` and the model documents `position=0` as the cover.
The cover resolver was never updated to honour it — it still orders by `created_at`.
Under the "import ALL images" model, importing many assets means `created_at.asc()`
returns whichever happened to be written first (often a swatch/listing thumbnail or a
scraped generic), not the intended `position=0` cover. **Ordering/cover assumptions
the resolver relies on are stale.**

**Phase 0.3 debug log line (to be merged in Phase 4, DEBUG level):** add at the
resolution point inside `ensure_product_cover_on_r2` (and/or at overlay-build time in
`twick_compositor_adapter`):
```python
logger.debug(
    "overlay resolve: block_id=%s product_id=%s asset_id=%s resolved_url=%s",
    block_id, product_id, asset_id, resolved_url,
)
```
I did not insert it in this diagnostic phase (Phase 0 = no code). It is staged for
Phase 4 per spec § 4.4.

Silent fallback found: **YES** — `video_compositor.py:480–519` (TrendingProduct
generic cover) and `:541–548` (arbitrary `media_keys` image). Phase 4 must remove
these silent substitutions and fail the block (`product_asset_unresolved`) instead.

---

## 0.4 Recommendation

This is a **composer-caused** defect cluster; no WaveSpeed escalation is needed. All
five raw speaking clips arrive in sync, non-black, non-frozen, and correctly lengthed
(0.1 deltas +0.01% … +0.73%, all inside the −2%/+5% speaking tolerance). The five
production defects map cleanly onto the planned phases:

- **Defect #1 (walks backward / ping-pong):** `block_extension._short_tail_reverse`
  + the dormant `block_normalize.build_pingpong_video_filter`. → **Phase 1**: delete
  both, switch motion blocks to overshoot (slot × 1.15) + trim-to-slot.
- **Defect #2 (lip desync):** the post-compose remux re-anchors audio to nominal slot
  `s` while extension/padding alters video block lengths (0.2). → **Phases 1–2**
  remove all retiming/padding so block video length == slot, eliminating the drift.
  Per § 1.2/§ 2.5, keep `fps=` (duration-preserving) and never reverse/stretch.
- **Defect #3 (black block, silent pass-through)** and **#4 (frozen face):** no
  structural/black/freeze gate exists on baked clips today (only an ad-hoc freeze
  helper in `render_providers`/`render_dispatcher`). → **Phase 3**: add
  `validate_baked_clip()` in `services/media_processing.py` (existing ffprobe home),
  fail the block + fail the cast render with enumerated reasons, no placeholder.
- **Defect #5 (wrong product image):** `ensure_product_cover_on_r2` proxies the
  generic TrendingProduct cover (Step 2) and orders ProductAssets by `created_at`
  instead of `position` (Step 3), ignoring the `position=0 cover` contract. →
  **Phase 4**: resolve by the block's product + `position`/cover semantics, delete
  the silent stock/`media_keys` fallbacks, assert the asset belongs to the cast's
  linked products, fail with `product_asset_unresolved` otherwise.

**Spec deviation to flag for review:** the spec § 0.2 / § 1.2 instruct edits in
`cast_ffmpeg_composer.py`, but the reverse/ping-pong/freeze/stretch logic actually
lives in `block_extension.py`, `block_normalize.py`, and `cast_render.py`
(`cast_ffmpeg_composer.py` was refactored to pure timeline translation). Phase 1's
deletions and the motion trim must therefore target those three modules. The motion
overshoot request (§ 1.1) belongs in the motion provider-dispatch layer
(`render_providers.py` Kling/Wan duration selection + `wan_body_motion.py`, which
currently clamps to int[2,15]). I recommend proceeding on that basis; no scope
change, only a filename correction. Phase 1.3 micro-slowdown stays **gated off**
(`MOTION_MICRO_SETPTS_ENABLED=false` scaffold only).

PHASE_0_COMPLETE — AWAITING REVIEW
