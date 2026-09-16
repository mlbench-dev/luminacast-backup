# Audio Sync Audit — Phase C

## Investigation Summary

**Reported bug:** Blocks approved in Script phase don't match what Audio generation produces.

### Theory 1: Deleted block leak
**Status: PARTIALLY CONFIRMED**

- The main generate (`_generate_cast_async`, line 166) and TTS-only (`_generate_tts_only`, line 478) paths correctly filter `b.deleted_at is None`.
- The video generation path (`_submit_video_jobs_for_cast`, line 787) correctly filters.
- **BUG FOUND:** The compositor/recomposite path (line 1201) filters only `is_active` but NOT `deleted_at`:
  ```python
  blocks = sorted(
      [b for b in cast.blocks if getattr(b, 'is_active', True)],  # MISSING deleted_at!
      key=lambda b: b.position,
  )
  ```
  This means recomposite can include deleted blocks.

### Theory 2: Variant script_text staleness
**Status: CONFIRMED — PRIMARY ROOT CAUSE**

When user edits a block's script in the Script phase:
- Frontend calls `PUT /api/casts/{id}/blocks/{block_id}/variants/{variant_id}` with new `script_text`
- This updates `Variant.script_text` (line 990 of casts.py)
- However, if the variant already has TTS audio (`audio_key` set), that audio is stale
- The TTS-only path (`_generate_tts_only`) skips variants with status=READY and audio_key set (line 549)
- **Result:** After editing script and re-running TTS, the old audio plays because the variant was marked READY

Fix: Before TTS dispatch, invalidate audio for any variant whose script_text changed since last TTS.

### Theory 3: Rogue LLM calls during audio gen
**Status: NOT CONFIRMED**

Neither `generate_cast_clips` nor `_generate_tts_only` call any LLM. They only do TTS via FishAudio. No bug here.

### Theory 4: Order drift
**Status: NOT CONFIRMED**

- Cast model has `order_by="Block.position"` on the blocks relationship
- All generation paths sort by position explicitly or via relationship ordering
- No order bug found.

## Root Cause

**Primary:** Variant script_text staleness — when user edits script and re-runs TTS, variants with existing audio_key are skipped (treated as READY). The old audio persists.

**Secondary:** Deleted blocks leak into recomposite path (missing `deleted_at is None` filter).

## Fixes Applied

1. Added `b.deleted_at is None` filter to compositor block query (line 1201)
2. Before TTS dispatch, propagate script edits: reset variant status to PENDING and clear audio_key when script_text has been updated since last TTS generation
3. Added explicit `ORDER BY Block.position` verification comment

