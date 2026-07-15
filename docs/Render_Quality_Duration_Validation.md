# Render Quality — Duration Overshoot, Trim, Validation Gate, Lip-Sync & Product Overlay

**Workstream:** Cast render quality (undershoot / reverse-walk / black block / frozen face / desync / wrong product image)
**Repo:** github.com/3gorka72/luminacast-omni
**Protocol:** Follow RULES.md Section 9 deploy protocol exactly (git pull on VPS → `docker compose build --no-cache` → verify container timestamp). Do not report "deployed" without completing all three steps.
**General rules:** Every Python `except` must call `sentry_sdk.capture_exception(e)`. No engine/provider names in user-facing strings. No hardcoded render timeouts — dynamic only (audio_duration × quality step-time + cold-start buffer). Diagnose before fixing. Report findings at each gate before proceeding.

**File-map correction (post-Phase-0):** The spec originally named `cast_ffmpeg_composer.py` as the home of the reverse/ping-pong/freeze/setpts logic. The Phase 0 audit established that file is now pure timeline→FFmpeg translation. The actual timing-altering logic lives in:
- `services/block_extension.py` — `_short_tail_reverse` (193–268), `_micro_pad` `tpad=clone` (143–190), ping-pong dispatch (480–549), reverse fallback (671–688)
- `services/block_normalize.py` — `build_pingpong_video_filter` (54–141), setpts video stretch (135–138), `extend_to_slot` call site (308–323)
- `tasks/cast_render.py` — `_hold_last_frame` (895–918), `_extend_compose_video_to_expected` (920–976), `_post_compose_audio_remux` (980+)
- `services/render_providers.py` + `services/wan_body_motion.py` — motion provider duration selection (Phase 1.1 home)

Read every "`cast_ffmpeg_composer.py`" reference in §0.2 / §1.2 / §5 grep-proof as "the three modules above" unless explicitly about timeline translation (concat/overlay/captions/carousel).

---

## Background (why)

Latest test render exhibited five defects:

1. Avatar walking backward / ping-pong motion — caused by an FFmpeg `reverse` filter used to pad clips that come back 10–40% shorter than their slot.
2. Lips out of sync on most speaking blocks.
3. One block rendered as black screen, but composition proceeded anyway (silent failure).
4. One block with a frozen/still face.
5. Wrong product image at 0:23 (generic stock shampoo instead of the imported product).

Decision (locked): freeze-last-frame and reverse/ping-pong padding are **rejected**. The strategy is **generate longer than needed, then trim**. Short clips are a failure condition, not something to pad around.

---

## Phase 0 — Diagnostics (NO code changes; report findings before proceeding)

**Gate: produce a written diagnostic report before starting Phase 1.**

### 0.1 Raw lip-sync check (speaking blocks)

For the failed test cast, locate the **raw WaveSpeed InfiniteTalk output clips** as stored before composition (R2 baked-clip storage). For each speaking block:

1. Download the raw clip.
2. Play it / inspect: is the lip sync correct **in the raw provider output**?
3. Run `ffprobe -v error -show_entries stream=codec_type,r_frame_rate,nb_frames,duration -show_entries format=duration -of json <clip>` and record: container duration, video stream duration, fps, frame count.
4. Compare raw clip duration to the block's TTS audio duration. Record the delta as a percentage.

### 0.2 Composer timing audit

Open `cast_ffmpeg_composer.py`. Locate and document (file:line) every filter or flag that alters clip timing:

- the `reverse` / ping-pong padding branch,
- any `setpts` usage (especially stretching video to slot length),
- any `atempo` usage,
- any forced output frame rate (`-r`, `fps=` filter) applied during concat/composition,
- how block audio is laid relative to block video (embedded clip audio vs. separately muxed TTS track, and what timestamp anchors it).

### 0.3 Product overlay resolution trace

Locate the code path that resolves the product image for a product overlay/carousel in a block (search for ProductAssets usage in the compositor / overlay builder). Document:

- how the block references the product asset (asset id? product id + index? cover flag?),
- any fallback branch that substitutes a stock or placeholder image when the asset is missing or fails to download,
- whether the "import ALL images to ProductAssets" change altered ordering/cover assumptions this code relies on.

Add a temporary (or permanent, behind DEBUG level) log line at resolution time: `block_id, product_id, asset_id, resolved_url`.

### 0.4 Diagnostic report

Deliver a short report:

- For each speaking block: raw clip in sync? duration delta vs audio?
- Composer: list of timing-altering operations with file:line.
- Product overlay: where did the stock shampoo URL come from (exact branch)?

**Decision matrix from 0.1/0.2:**
- Raw clips IN sync + composer stretches/pads → desync is composer-caused → fixed by Phases 1–2 (no provider work needed).
- Raw clips OUT of sync → provider-side issue → report immediately; do not attempt FFmpeg workarounds; we will escalate to WaveSpeed separately.

---

## Phase 1 — Motion blocks: overshoot + trim; remove reverse padding

Applies to: `avatar_motion` / `avatar_acting` (and merged `avatar_action`), i.e. fal.ai Wan 2.5 T2V and Kling 2.5 Turbo Pro clips. Does NOT apply to speaking blocks.

### 1.1 Overshoot request logic

In the provider-dispatch layer for motion generation (locate where Wan/Kling duration parameters are set):

1. Define per-provider supported duration tiers in one place (config/constant, not scattered):
   - `WAN_25_T2V_DURATIONS = [5, 10]` (verify against current fal.ai API docs before hardcoding — confirm actual supported values)
   - `KLING_25_TURBO_DURATIONS = [5, 10]` (verify likewise)
2. Compute: `target = slot_duration * 1.15` (constant `MOTION_OVERSHOOT_FACTOR = 1.15`, env-overridable).
3. Request the **smallest supported tier ≥ target**. If target exceeds the max tier, use existing multi-segment chaining; apply the overshoot only to the **final** segment's tier selection.
4. Log requested tier vs slot duration per block.

### 1.2 Trim in composer

In `cast_ffmpeg_composer.py`:

1. **Delete** the reverse/ping-pong padding branch entirely (identified in 0.2). Delete, do not flag-disable.
2. **Delete** any freeze-last-frame padding branch if present.
3. For motion clips longer than slot: trim **from the start of the clip**, keeping the head: `trim=duration={slot_duration}` (+ `setpts=PTS-STARTPTS`) in the filtergraph, or `-t {slot_duration}` if the clip is processed standalone before concat. Keep PTS handling consistent with the rest of the graph.
4. Remove any `setpts` stretching of video to fill slot length (if found in 0.2). Video length must equal slot length by trim, never by retiming.

### 1.3 Micro-slowdown fallback — APPROVED 2026-06-11

If a motion clip arrives shorter than slot despite overshoot:

- If shortfall ≤ 5%: apply `setpts=(slot/clip)*PTS` slowdown (imperceptible range) and proceed, with a Sentry breadcrumb + log.
- If shortfall > 5%: fail validation (Phase 3 handles).
- **Never apply to speaking blocks** (audio sync is sacred).
- Default `MOTION_MICRO_SETPTS_ENABLED=true` (was `false` during scaffold). Env-overridable.

---

## Phase 2 — Speaking blocks: duration tolerance + retry, no padding ever

Applies to: `avatar_speaking` (WaveSpeed InfiniteTalk cascade only).

1. After a speaking clip is generated, compare clip video duration to the block's TTS audio duration.
2. Tolerance: clip must be within **−2% / +5%** of audio duration (constants, env-overridable).
3. If outside tolerance: retry the provider call **once** (log + Sentry breadcrumb). If still outside: mark block failed (Phase 3 semantics). Do not stretch, freeze, reverse, or pad.
4. If clip is slightly LONG (within +5%): trim from the end to audio duration.
5. Verify the composer uses the speaking clip's timing as-is — TTS audio and clip video must share the same start anchor in the timeline, with no fps re-stamping that changes effective duration (per 0.2 findings; if a forced `-r`/`fps=` conversion changes duration, fix it to preserve duration, e.g. proper fps filter rather than container-level re-stamp).

---

## Phase 3 — Baked-clip validation gate (composition must never include a bad clip)

Create `validate_baked_clip(path, block)` in the media/validation layer (suggested home: `media_processing` module; do not create a new top-level module if an obvious existing home exists). Run it on **every** baked clip immediately after generation/download, before the clip becomes eligible for composition.

Checks (all via ffprobe/ffmpeg subprocess, parse output programmatically):

1. **Structural:** video stream exists; `nb_frames > 0`; duration > 0.
2. **Minimum duration:** duration ≥ `CLIP_VALIDATE_MIN_DURATION_S` (0.5s structural floor for all clip types), AND
   - **motion blocks — two-tier gate (implemented):** the 0.5s structural floor is far too lenient for motion (a 1.07s clip for a 3.63s slot passed it on `rnd_7923dde6c01f`), so motion clips get an additional two-tier check, evaluated AFTER the Phase 1.3 micro-slowdown has been applied:
     - **structural minimum:** duration ≥ `MOTION_STRUCTURAL_MIN_DURATION_S` (default `2.0`, env-overridable). Below this → reason `motion_clip_too_short` (the provider returned garbage — distinct from a near-miss).
     - **slot floor:** duration ≥ `slot_duration × (1 − MOTION_DURATION_FLOOR_TOLERANCE)` (default tolerance `0.01` = ≤1% rounding slack, env-overridable). Below this → reason `duration_undershoot` (a near-miss the micro-slowdown could not lift to the slot). A clip LONGER than the slot is accepted — the composer head-trims the surplus.
   - **speaking blocks:** NOT subjected to the motion gate; their length is validated against the TTS **audio** duration per Phase 2 / §2.1 (`_enforce_speaking_tolerance`), not the slot.
3. **Black detection:** `ffmpeg -i clip -vf "blackdetect=d=0.5:pix_th=0.10" -an -f null -` → sum black intervals; reject if black coverage > 80% of clip duration.
4. **Freeze detection:** `freezedetect=n=0.001:d=2.0` → reject if frozen coverage > 70% of clip duration (this catches the "still face" defect).
5. **Audio (speaking blocks only):** audio stream exists if the pipeline expects embedded audio; otherwise verify the paired TTS file exists and is non-empty.

Thresholds as named constants, env-overridable.

**Failure semantics:**

- Validation fail → retry generation once → fail again → block status `failed` with reason string (e.g. `black_frame_coverage`, `duration_undershoot`, `zero_frames`).
- If any block is `failed`, the **cast render fails** with an error enumerating the failed blocks and reasons. Composition with a missing/placeholder block is forbidden.
- All rejects go to Sentry with block id, provider, reason, and ffprobe summary.
- User-facing error text: neutral, no provider/engine names ("Block 4 failed quality validation (video too short)" style).

---

## Phase 4 — Product overlay asset wiring

Based on 0.3 findings:

1. Fix the resolution bug so the overlay uses the ProductAsset actually referenced by the block (correct id/index/cover semantics under the "all images imported" model).
2. **Remove any silent stock/placeholder fallback.** If the referenced asset is missing or its download fails: retry download once, then fail the block via the Phase 3 failure semantics (reason `product_asset_unresolved`). Never substitute an unrelated image.
3. Add an assertion at compose time: every overlay asset id must belong to the cast's linked products; violation → fail with Sentry capture.
4. Keep the per-block resolved-URL log line from 0.3 (DEBUG level).

---

## Phase 5 — Deploy + verification

1. Deploy per RULES.md Section 9 (pull → `docker compose build --no-cache` → confirm container build timestamp). Paste the timestamp verification output in your report.
2. Render a test cast containing at minimum: 2 speaking blocks, 1 motion block with slot duration between provider tiers (e.g. 7–8s, forcing 10s overshoot + trim), 1 block with a product overlay from an imported product with multiple images.
3. Verification checklist (report each item):
   - [ ] No reversed/ping-pong motion anywhere; motion block plays forward and fills its slot exactly.
   - [ ] `reverse` filter and freeze-pad code paths no longer exist in `cast_ffmpeg_composer.py` (grep proof).
   - [ ] Speaking blocks: lips in sync in the final composed video AND raw clip durations logged within tolerance.
   - [ ] Deliberately corrupt/black test: feed a black clip through `validate_baked_clip` → block fails, cast render fails with enumerated reason (no composed output).
   - [ ] Product overlay shows the correct imported product image; resolved-URL log line confirms asset id belongs to the cast's product.
   - [ ] UsageEvent rows logged for the overshoot seconds (cost tracking must reflect the longer requested durations).
4. Note in the report: overshoot increases motion-block provider cost by ~15–25% (billed at requested tier). Confirm `/admin/costs` reflects requested-tier seconds, not slot seconds.

---

## Out of scope (do not touch in this workstream)

- Two-pass script generation, captions/WhisperX per-block, music/SFX, publish/Zernio, live streaming.
- Voice post-processing chain (separate instruction doc).
- Provider cascade changes beyond duration parameters.
