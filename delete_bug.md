# A1 — Deleted Blocks Still Rendering

## Root Cause

The DELETE endpoint at `backend/orchestrator/routers/casts.py:835` performed a **hard delete** (`db.delete(block)`), which permanently removes the row. However, the GET cast endpoint at line 141 returned ALL blocks without any filtering, and block iteration across the codebase was inconsistent — some paths filtered by `is_active`, others didn't.

The frontend (`ScriptBlockEditor.tsx:464`) does optimistic deletion (removes from local state immediately), then calls the API. If the user navigates away and comes back, or if generation runs before the deletion propagates, deleted blocks would still appear.

## Investigation Findings

| Finding | File | Lines |
|---------|------|-------|
| DELETE endpoint (hard delete) | `routers/casts.py` | 835-848 |
| Block model (no `deleted_at`) | `models/block.py` | 50-99 |
| GET cast (no filtering) | `routers/casts.py` | 141-157 |
| Frontend delete (optimistic + API) | `ScriptBlockEditor.tsx` | 464-467 |
| Generation filter (has `is_active`) | `tasks/generate_cast.py` | 132 |

## Fix Applied

1. **Added `deleted_at` column** to Block model (`models/block.py`) — `DateTime, nullable=True, indexed`
2. **Migration** `a1b2c3d4e5f6_add_blocks_deleted_at.py` — adds column + index
3. **Soft delete** — DELETE endpoint now sets `deleted_at = utcnow()` and `is_active = False` instead of `db.delete(block)`
4. **Filtered blocks everywhere:**
   - GET cast response: skips blocks where `deleted_at is not None`
   - `tasks/generate_cast.py`: all 3 block-loading lines now filter `b.deleted_at is None`
   - Scene object migration, TTS variants endpoint, whisper transcription, recomposite: all filter deleted blocks
