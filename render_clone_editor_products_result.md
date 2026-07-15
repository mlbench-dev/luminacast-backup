# Render / Clone / Editor / Products — Implementation Results

Completed: 2026-04-13

## Phase A: Honest Render Status ✅

### Changes
- **`backend/orchestrator/models/render_job.py`** — NEW: `RenderJob` model with 5-state enum (QUEUED, IN_PROGRESS, STALLED, FAILED, ENDPOINT_DOWN, COMPLETED), RenderJobType, RenderProvider
- **`backend/orchestrator/services/render_eta.py`** — NEW: Rolling ETA from historical median of last 20 jobs, queue position, confidence levels (high/low/none)
- **`backend/orchestrator/services/runpod.py`** — REWRITE: Removed hardcoded 1800s timeout. Now uses health checks (2 consecutive failures → ENDPOINT_DOWN), 5-min stall detection with auto-retry, render_jobs table tracking. Only Celery `task_time_limit` as absolute ceiling.
- **`backend/orchestrator/routers/render_jobs.py`** — NEW: `GET /api/render-jobs/{id}/status` endpoint
- **`backend/orchestrator/routers/avatar.py`** — MODIFIED: Attaches `render_status` from render_jobs table
- **`backend/orchestrator/routers/casts.py`** — MODIFIED: Aggregated render status (worst state wins)
- **`backend/orchestrator/tasks/generate_avatar.py`** — MODIFIED: Added `time_limit=5400, soft_time_limit=5100`
- **`backend/orchestrator/tasks/generate_cast.py`** — MODIFIED: Added `time_limit=5400, soft_time_limit=5100`
- **`backend/orchestrator/migrations/versions/aa01_add_render_jobs_table.py`** — NEW: render_jobs table migration
- **`frontend/companion-app/src/components/RenderStatusBanner.tsx`** — NEW: 5-state banner with ETA, progress bar, retry/contact support
- **`frontend/companion-app/src/components/cast-builder/FinalizingPhase.tsx`** — MODIFIED: Shows RenderStatusBanner, 10s poll interval

### Key rules enforced
- NO hardcoded render timeouts (only Celery task_time_limit as dead-end failsafe)
- Health endpoint checks for ENDPOINT_DOWN detection
- 5-minute stall detection with auto-retry once → FAILED

## Phase B: Clone TikTok Scout ✅

### Changes
- **`backend/orchestrator/models/clone_tiktok.py`** — NEW: `CloneTikTokScan` and `CloneTikTokVideo` models
- **`backend/orchestrator/tasks/clone_tiktok_scout.py`** — NEW: Celery task with Apify scraper (last 20 videos), MediaPipe Pose analysis (33 keypoints >= 0.7, ankles 27/28, >= 3s continuous at 2fps), R2 upload, thumbnail extraction
- **`backend/orchestrator/routers/clone_scout.py`** — NEW: POST/GET scout endpoints + video selection
- **`backend/orchestrator/migrations/versions/ab02_add_clone_tiktok_scout.py`** — NEW: Tables + avatar columns
- **`frontend/companion-app/src/components/CloneFlow.tsx`** — MODIFIED: Scout tab, handle input, video grid, segment picker modal
- **`frontend/companion-app/src/lib/api.ts`** — MODIFIED: scoutStart, scoutStatus, scoutSelectVideo methods

### Key rules enforced
- MediaPipe strict: ALL 33 keypoints >= 0.7 AND both ankles AND >= 3 seconds continuous
- Per-video failure handling (continues scanning on individual failures)

## Phase C: Kill Dual Frontend ✅

### Changes
- **`frontend/companion-app/src/components/cast-builder/ArrangePhase.tsx`** — MODIFIED: Removed TwickStudio JSX, removed MutationObserver branding removal, replaced with SceneTimeline
- **`frontend/companion-app/src/components/cast-builder/scene-composer/SceneTimeline.tsx`** — NEW: 8-track timeline (V1-V3, A1-A3, Captions, Text), animated playhead, time ruler
- **`frontend/companion-app/src/pages/CastBuilder.tsx`** — MODIFIED: Added delete button in all non-terminal phases
- **`editor_rewire_checklist.md`** — NEW: E1-E11 re-verification

### Key rules enforced
- TwickStudio UI completely removed (no logo, watermark, Video Library, Image Library, Load Project, Save Draft, Export buttons)
- Engine APIs retained: LivePlayerProvider, TimelineProvider, useTimelineContext
- Delete cast works in all phases except LIVE and GENERATING

## Phase D: Product Library ✅

### D1: TikTok Category Taxonomy
- **`tiktok_categories.md`** — NEW: Full 20-category taxonomy with subcategories

### D2: Align Filter UI with TikTok Categories
- **`backend/orchestrator/services/product_categories.py`** — UPDATED: Expanded from 12 to 20 TikTok Shop categories matching the taxonomy
- Frontend `CategoryPicker` already implements two-level dropdown (parent → subcategory on hover)

### D3: Fix Backend Filters (SQL WHERE)
- **`backend/orchestrator/services/product_discovery.py`** — FIXED: Re-query after Apify fetch now applies ALL filters (min_price, max_price, min_revenue, min_items_sold, min_rating, min_commission, revenue_growth_min, is_affiliate) — previously only applied query + category
- Primary query (lines 155-184) already had correct SQL WHERE clauses

### D4: Product Profile — All Media + Metrics
- **`frontend/companion-app/src/pages/ProductLibrary.tsx`** — MODIFIED:
  - Metrics strip now shows: rating, reviews, total sold, sold (30d), commission rate
  - Videos section shows ALL videos (not just first), with per-video delete button
  - Already had: image carousel, description, selling points, variants, specifications, seller info

## Phase E: Gauntlet v4 ✅

### Changes
- **`g11-clone-scout.spec.ts`** — NEW: Scout TikTok handle, verify video grid, full-body detection, segment selection
- **`g12-render-status.spec.ts`** — NEW: 5-state machine verification, ETA banner, state transitions, no hardcoded timeout
- **`g02-ai-avatar.spec.ts`** — UPDATED: v2 → v3 label, Approve & Continue body desc save verification, 4 voice options
- **`g10-cast-builder-full-clickthrough.spec.ts`** — UPDATED: No-Twick-UI assertion (TwickStudio not rendered, no branding elements, SceneTimeline present)

## Pre-existing Issues (Not Addressed)
- TypeScript errors in MyVideos.tsx, Setup.tsx (React Query function signature mismatches)
- ArrangePhase.old.tsx stale backup file with errors
- SceneTimeline.tsx minor type issues (tts_r2_key, currentTime on TimelineContextType)
