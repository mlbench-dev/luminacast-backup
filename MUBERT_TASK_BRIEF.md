# Mubert Music + SFX + Captions + Bonded Tracks — Implementation Brief

**Workspace path:** `/home/user/workspace/luminacast-omni-6bbb2788`
**Repo:** https://github.com/3gorka72/luminacast-omni
**Live URL:** https://luminacast.com
**VPS:** root@145.223.121.28, pw `ZlNn'MYV2+.ph+PA`, deploy dir `/opt/luminacast-omni`

## Specs to follow (read these in /home/user/workspace/)
- `Music_SFX_Knowledge_Base_v1.md` — knowledge base (mood→tags, SFX, prosody, platform rules, catalog presets)
- `Perplexity_Music_Mubert_SFX.md` — Music tab + SFX system blueprint
- `Perplexity_Mubert_SFX_Integration.md` — Mubert + SFX deeper detail
- `Perplexity_Captions_Bonded_Tracks-1.md` — Captions overhaul + bonded tracks (FIX 1–6)

## Order of execution
1. **Stage 1 — Mubert + SFX backend + Music page** (Perplexity_Music_Mubert_SFX.md + Knowledge Base) → commit + deploy
2. **Stage 2 — Captions FIXES 1, 2, 3 (Phase A), 4, 6** → commit + deploy
3. **Stage 3 — FIX 5 BONDED TRACKS (do LAST per user instruction)** → commit + deploy

## ⚠️ Mubert API VERSION CORRECTION

The specs mention Mubert v2 (`api.mubert.com/v2/RecordTrackTTM`) — **DO NOT USE THAT**. The user's credentials are for Mubert API **v3**:

- Base URL: `https://music-api.mubert.com/api/v3/`
- Service auth headers: `company-id` + `license-token`
- Customer flow:
  1. `POST /api/v3/service/customers` with `company-id` + `license-token` headers, body `{"custom_id": "<our-user-id>"}` → returns `data.access.customer_id` and `data.access.token` (the access-token). Cache these per user (e.g., on User model `mubert_customer_id`, `mubert_access_token` columns).
  2. `POST /api/v3/public/tracks` with `customer-id` + `access-token` headers, body `{"prompt": "...", "duration": 60, "bitrate": 128, "format": "mp3", "intensity": "medium", "mode": "track"}` → returns `data.id` (TRACK_ID) and `generations[0].status = "processing"`.
  3. Poll `GET /api/v3/public/tracks/{TRACK_ID}` with the same customer/access headers; when `generations[0].status == "done"`, `generations[0].url` has the MP3 URL.

**Use a `prompt` field, not `tags` array.** The v3 API takes a natural-language prompt. Build the prompt from the knowledge-base mood→tags mapping (e.g., for mood=`excited`, prompt = `"energetic upbeat pop bright"`). Keep `intensity` from the knowledge base.

**Credentials (already in VPS .env, mirror to backend/orchestrator/config.py):**
- `MUBERT_COMPANY_ID=a1a98edf-7271-472f-a8b6-8c49d216d578`
- `MUBERT_LICENSE_TOKEN=iYp5ubW5YHqVn9YnYgWV2TOlCPKvs2pL6c6bvAQQooJAgxTPTwQ7ofak0FlMNkbD`

## Existing codebase context (DO read these before starting)

```
backend/orchestrator/services/        — existing services (pexels.py, openrouter.py, etc.)
backend/orchestrator/services/ai_prompts.py — knowledge base + prompts live as dict entries here, served via /api/preadmin/prompts. ADD the Music_SFX_Knowledge_Base_v1.md content as new prompt entries so admins can edit.
backend/orchestrator/scripts/seed_sfx_library.py — already exists, generates synthetic ffmpeg tones for SFX; rewrite to use the 18-clip catalog from Music_SFX_Knowledge_Base_v1.md Section 2.
backend/orchestrator/routers/music.py — EXISTING router with sound-cast (ACE-Step) routes. ADD new routes here (catalog, generate, sfx-library, upload). Don't remove the sound-cast routes.
backend/orchestrator/models/cast.py — Cast model. ADD background_music_url, background_music_mood, background_music_tags, caption_preset.
backend/orchestrator/models/user.py — ADD mubert_customer_id, mubert_access_token (so we don't create a new Mubert customer per request).
backend/orchestrator/engine/cast_generator.py — generate_scripts is here. Inject SFX prompt instructions into the cast_script_generator system prompt.
backend/orchestrator/tasks/ — Celery tasks. ADD music catalog refresh task.
backend/orchestrator/tasks/cast_render.py — FFmpeg render. ADD music ducking + SFX mixing + caption-style reading from timeline data.
frontend/companion-app/src/pages/Music.tsx — EXISTING ACE-Step page. Refactor: 4 tabs (Browse / AI Generate / SFX / Uploaded) + ACE-Step parked behind a feature flag at the bottom.
frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/captions-layer.tsx
  + caption-page.tsx — existing Remotion caption components. Wire them up properly via createTikTokStyleCaptions.
frontend/companion-app/src/lib/editorStarterMapping.ts — currently creates 5-word TextItem chunks for captions. Replace with single CaptionsItem per block (FIX 1).
```

## DB migration approach

The repo's Alembic has cycle issues. Use **idempotent ALTER TABLE in main.py lifespan** (the established pattern). Search `idempotent` and `lifespan` in main.py to see the existing approach:

```python
# Add to main.py lifespan startup (concept):
async with engine.begin() as conn:
    await conn.execute(text("ALTER TABLE casts ADD COLUMN IF NOT EXISTS background_music_url TEXT"))
    await conn.execute(text("ALTER TABLE casts ADD COLUMN IF NOT EXISTS background_music_mood VARCHAR(40)"))
    await conn.execute(text("ALTER TABLE casts ADD COLUMN IF NOT EXISTS background_music_tags JSON"))
    await conn.execute(text("ALTER TABLE casts ADD COLUMN IF NOT EXISTS caption_preset JSON"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS mubert_customer_id VARCHAR(80)"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS mubert_access_token TEXT"))
```

## Hard rules (project conventions)

- **No hardcoded timeouts** — Celery `task_time_limit` only.
- **Every `except` block calls `sentry_sdk.capture_exception(e)`**.
- **No engine names in user-facing strings** (e.g. don't say "Mubert" to users — say "AI music"; don't say "InfiniteTalk" — say "AI avatar").
- **`docker compose restart` does NOT reload .env** — use `up -d --force-recreate`.
- **Frontend `toast` import path is `@/hooks/useToast`**, NOT `@/components/ui/toast`.
- **TS strictness:** `noUnusedLocals: false` (so unused imports don't break build, but keep imports tidy).
- **Bundle name format:** `index-XXXXXX-v2.js`. Verify the bundle hash changes after each deploy.

## Deployment recipe (run after EACH stage)

```bash
cd /home/user/workspace/luminacast-omni-6bbb2788
git add -A && git commit -m "..."
git push origin main   # use api_credentials=["github"]

sshpass -p "ZlNn'MYV2+.ph+PA" ssh -o StrictHostKeyChecking=no root@145.223.121.28 \
  "cd /opt/luminacast-omni && git pull origin main && \
   docker compose build --no-cache orchestrator celery-worker celery-render-worker celery-beat frontend && \
   docker compose up -d --force-recreate orchestrator celery-worker celery-render-worker celery-beat frontend && \
   docker compose restart nginx"

# Smoke test
sleep 8
sshpass -p "ZlNn'MYV2+.ph+PA" ssh -o StrictHostKeyChecking=no root@145.223.121.28 \
  "curl -s -o /dev/null -w 'HEALTH=%{http_code}\n' https://luminacast.com/health; \
   curl -s -o /dev/null -w 'API_HEALTH=%{http_code}\n' https://luminacast.com/api/health; \
   curl -s https://luminacast.com/ | grep -oE 'index-[A-Za-z0-9_-]+\\.js' | head -1"
```

## Pragmatic scope — what to skip if time runs out

If you can only finish part of this in the time available, **prioritize in this exact order**:

1. ✅ Mubert v3 service + auto-generate music on cast creation (most user value)
2. ✅ Music tab UI: Browse / Generate / SFX library (visible to user immediately)
3. ✅ Park ACE-Step behind feature flag (avoids confusion)
4. ✅ Captions FIX 1 — CaptionsItem per block (HUGE bug — fixes the word-fragment timeline)
5. ✅ Captions FIX 4 — remove duplicate from inspector (5-min cleanup)
6. ⚠️ Captions FIX 2/3 — presets + FFmpeg style read
7. ⚠️ SFX extraction + FFmpeg mixing
8. ⚠️ FIX 5 — bonded tracks (do LAST as user requested)

**Things you SHOULD NOT do (out of scope for this session):**
- Don't actually source/upload 18 SFX clips to R2 — keep the existing `seed_sfx_library.py` synthetic tones as a placeholder. Document where real CC0 clips need to go (s3://luminacast/sfx/).
- Don't implement the catalog batch generation Celery beat job. The schema + endpoint is enough. Mark as TODO.
- Don't implement live stream music. Out of scope here.
- Don't implement Remotion SSR caption overlay (Phase B). Phase A (FFmpeg reads preset) is sufficient.
- Don't change InfiniteTalk / TTS pipelines.

## Reporting back

After each stage, summarize:
- Commit hash
- New bundle hash (for frontend stages)
- Files touched
- Any deviations from the spec (and why)
- What's still TODO

Good luck. Make every commit small, descriptive, and deployable on its own.
