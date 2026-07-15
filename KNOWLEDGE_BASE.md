# LuminaCast Omni — Knowledge Base for Session Continuity

---

## Bug Investigation Protocol (READ FIRST)

Every bug follows this sequence. No shortcuts.

1. **Gather evidence** — `docker compose logs`, `git log --oneline -10`, DB state queries, Sentry issues, Better Stack search by avatar_id/cast_id
2. **Identify exact failure point** — file:line, full traceback, DB record state (status, progress_step, generation_error)
3. **Report findings before proposing fix** — exact error + root cause + proposed fix. Do not guess.
4. **Research before coding** — known library gotchas, ecosystem patterns, check if same bug was fixed before (search STATUS.md)
5. **Fix → verify in running container** — `docker compose exec -T orchestrator grep "pattern" /app/file.py`
6. **Loop-fix pattern**: fail → paste exact error + current code diff in STATUS.md → re-read → new fix → **cap at 3 iterations** → stop with PENDING DECISION, do not proceed

---

## Architecture

### Infrastructure
- **VPS**: 145.223.121.28 — project at `/opt/luminacast-omni`, 16GB RAM, 193GB disk
- **GPU Server (HOSTKEY Iceland)**: 194.247.183.12 — RTX 4090 24GB, worker at `/opt/gpu-worker`, systemd `gpu-worker.service`
- **R2 CDN**: media.luminacast.com
- **Domain**: www.luminacast.com (SSL via nginx)
- **Repo**: github.com/3gorka72/luminacast-omni (branch: main)
- **Credentials**: ALL in `.env` on VPS and `/opt/gpu-worker/.env` on GPU server. Referenced by env var name only. See `.env.example` for the full list. **Never paste credentials into documents or LLM contexts.**

### Key Env Var Names (not values)
`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, `RUNPOD_ENDPOINT_IS_PUBLIC`, `R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL`, `FISH_AUDIO_API_KEY`, `ELEVENLABS_API_KEY`, `HF_TOKEN`, `APIFY_API_TOKEN`, `SENTRY_DSN_BACKEND`, `SENTRY_GPU_DSN`, `BETTERSTACK_TOKEN`, `BETTERSTACK_GPU_TOKEN`, `BETTERSTACK_DOCKER_TOKEN`, `OPENROUTER_API_KEY`, `GPU_SERVER_URL`, `GPU_SERVER_ENABLED`, `BS_ROFORMER_RENDER_RATIO`, `FISH_SPEECH_RENDER_RATIO`, `INFINITETALK_RENDER_RATIO`

### Database Architecture
- **PostgreSQL** = source of truth (avatars, casts, blocks, variants, products, users, api_usage_logs)
- **Neo4j** = intelligence layer (channel relationships, content graph) — synced fire-and-forget, never blocking
- **Redis** = Celery broker + cache (scraping jobs TTL)

### RunPod Endpoints (all min workers = 0)
- BS-RoFormer (48GB tier) — `2oivc3eustr3u5`
- Fish Speech (pre-built template) — `a92asrqrug50zt`
- InfiniteTalk (48GB tier, execution timeout 3600s) — `triazevwb6a8ap`

### GPU Server Services
BS-RoFormer (vocal isolation) + Fish Speech. **InfiniteTalk REMOVED from GPU server** — runs on RunPod only. PyTorch 2.11+cu128, python3.10 (NOT 3.11).

### Role Hierarchy
super_admin → support_agent → creator → operator

### 23 Locked Architecture Decisions (ADs)
Do not revisit. Documented in STATUS.md. Key ones: PostgreSQL as source of truth (AD-020), user_events as unified audit/analytics (AD-021), webhooks over polling for long jobs (AD-016), quality tiers at $14.99/$19.99/$29.99 (AD-014), MuseTalk for live PIP vs InfiniteTalk for batch (AD-024/025).

---

## Critical Lessons (Hard-Won, Numbered)

### Deployment & Docker

**L-01: Docker .env not reloading.**
`docker compose restart` does NOT reload .env. Must use `docker compose up -d --force-recreate <service>`.

**L-02: Nginx 502 after container restart.**
Container IPs change on recreate, nginx caches old DNS. Fix: `docker compose restart nginx` after recreating orchestrator.

**L-03: Never `docker compose down` before building.**
Build first while old containers run, then `docker compose up -d` to swap. `down -v` wipes the database.

**L-04: sed on Python files causes SyntaxError.**
Empty if blocks, broken indentation → crashes entire orchestrator → login fails. Always verify after editing. Use Python scripts for multi-line edits.

### Video & Audio Pipeline

**L-05: ffmpeg `-c copy` + non-keyframe `-ss` = corrupt frames.**
Must use `-ss` BEFORE `-i` with re-encode. Caused MediaPipe to find 0 faces on valid videos. (B-097)

**L-06: R2 uploads must include explicit `ContentType` ExtraArg.**
Default `octet-stream` breaks browser video playback. Always set `video/mp4`, `audio/wav`, etc.

**L-07: R2 secret key must be EXACTLY 64 characters.**
Truncation causes silent `SignatureDoesNotMatch` — worker processes audio fine, fails on upload.

**L-08: InfiniteTalk returns base64 video, not URL.**
RunPod worker returns `{"video": "AAAAIGZ0..."}`. Webhook handler must decode base64, upload to R2, set video_key.

**L-09: TTS audio must be uploaded to R2 before passing to InfiniteTalk.**
Use public CDN URL (`media.luminacast.com/...`), not signed R2 API URLs.

**L-10: ComfyUI resolution is hardcoded in template, not API param.**
Caused 720p-when-should-be-480p, triggering execution timeouts. Check SIZE_MAP maps correctly.

**L-11: Pre-InfiniteTalk background for static images, post-InfiniteTalk rembg for video backgrounds.**
i2v can't take video as conditioning input. Product overlays ALWAYS post-InfiniteTalk — diffusion distorts crisp pre-composited images.

### Voice Pipeline

**L-12: Voice race condition — female voice on male avatar.**
process-segment runs voice + image pipelines in parallel. Frontend must wait for BOTH facesReady AND voiceReady (voice_clone_progress >= 100) before triggering regenerate. Clone avatars NEVER fall back to preset voice — fail with error.

**L-13: Pyannote DiarizeOutput wrapping.**
Newer pyannote wraps result. Use `getattr(result, "speaker_diarization", result).itertracks(yield_label=True)`.

**L-14: Separate avatar creation from pipeline launch.**
Race condition with multiple Celery tasks on same avatar. `POST /avatar/create` then separate `process-segment` call.

### Async & Celery

**L-15: Celery + async event loop conflicts.**
`Task got Future attached to a different loop`. Use `_make_session_factory()` per-operation, not shared. Match pattern in generate_avatar.py.

**L-16: `fal_client.subscribe()` is SYNCHRONOUS.**
Blocks the entire FastAPI event loop from async routes. Wrap in `asyncio.to_thread()`. Use `fal_client.run_async()` instead.

### External APIs

**L-17: Apify `profiles` field expects BARE usernames, not URLs.**
Full URLs silently return default FYP results — no error.

**L-18: Apify trial caps at ~20 items.**
Must subscribe to paid plan. Code logs warning when <20 results.

**L-19: TikTok CDN URLs contain `?` and `&`.**
Always `urllib.parse.quote(url, safe='')` before building proxy URLs.

**L-20: ElevenLabs quirks.**
- `create-previews` requires 100+ char text
- Response uses `audio_base_64` (underscores)
- Preview IDs are temporary → must call `create-voice-from-preview` before TTS
- Use `/text-to-voice/design` with `model_id: "eleven_ttv_v3"` + `auto_enhance_description: True`
- Gender/language must be prepended to description

**L-21: OpenRouter LLM.**
Default was `meta-llama/llama-3-70b-instruct` (unreliable). Switched to `anthropic/claude-3-haiku`. Must check for `"error"` key before accessing `"choices"`.

**L-22: RunPod `is_public` must be EXPLICIT env var flag.**
Never string-match on endpoint ID — breaks when you change endpoints. Use `RUNPOD_ENDPOINT_IS_PUBLIC=false`.

### Frontend & Cast Builder

**L-23: Cast completing with 0 variants.**
Frontend creates cast + blocks but never creates Variant records. `PUT /api/casts/{id}/blocks` must create both Block AND Variant.

**L-24: NULL product_id in blocks.**
Frontend saves blocks without product_id → product overlay never runs. Must map product_id from selected products.

**L-25: Frontend polling must NOT be gated on current step.**
`enabled: currentStep === "faces"` stops polling when user advances, voice progress freezes.

**L-26: `face_ref_key` must be explicitly persisted** via `/select-face` endpoint. Can't rely on implicit saves.

**L-27: `cast.products` empty at CastBuilder step 1** because query is gated on `step >= 2`. Either ungate or fetch products directly.

**L-28: Webhooks > polling for long jobs.**
InfiniteTalk ~85:1 render ratio, 30s clip needs 3600s timeout. Fire-and-forget via webhook handler.

**L-29: Subagent code fixes getting overwritten.**
Always `git pull origin main` before changes. Verify running container code matches repo.

---

## Render Pipeline (6 steps, in order)

1. **TTS** (Fish Audio API, ~2-5s) → upload audio to R2
2. **InfiniteTalk** (RunPod 48GB, ~5-10min at 480p) → webhook returns base64 → decode → upload to R2
3. **Product overlay** (FFmpeg on VPS CPU, ~5-10s) → if block has product_id + cover image
4. **Text overlays** (FFmpeg drawtext, ~5s)
5. **SFX audio mix** (FFmpeg amix, ~5s)
6. **Upscale** (GPU server Real-ESRGAN + GFPGAN, optional for HD/HD+)

### Fallback Chains
- BS-RoFormer: GPU server → RunPod → CPU → raw audio
- Fish Speech TTS: GPU server → RunPod → Fish Audio cloud API
- InfiniteTalk: RunPod ONLY (no GPU server, no fallback)

### Dynamic Timeouts (in .env)
- `BS_ROFORMER_RENDER_RATIO=1.5` (16GB=3.0, 24GB=2.0, 48GB=1.5)
- `FISH_SPEECH_RENDER_RATIO=3.0` (16GB=4.0, 24GB=3.0, 48GB=2.0)
- `INFINITETALK_RENDER_RATIO=6.0` (24GB=15.0, 48GB=6.0, 80GB=3.0)
- Formula: input_duration × ratio + model_load(30s) + buffer(60s)
- GPU contention: health check → skip in 3s if busy with different model

### Quality Tiers (AD-014)
- Simple: $14.99, 480p standard
- HD: $19.99, 720p upscaled (Real-ESRGAN)
- HD+: $29.99, 1080p upscaled (Real-ESRGAN + GFPGAN face enhancement)

---

## Key Features & Routes

### Routes
`/my-avatar`, `/my-avatar/clone`, `/my-avatar/ai-avatar`, `/products`, `/cast-builder`, `/cast-builder/{id}`, `/live-control`, `/analytics`, `/settings/channels`, `/settings/team`, `/settings/billing`, `/admin`, `/preadmin` (prompt registry)

### Prompt Registry
All LLM prompts centralized in `services/ai_prompts.py`. Admin view at `/preadmin` (read-only). Never use inline prompt strings — always reference the registry.

### MuseTalk Architecture (AD-024/025)
- **InfiniteTalk** = batch rendering (cast clips, avatar test videos)
- **MuseTalk** = live PIP avatar (future, for Go Live streaming)
- Separate concerns: batch quality vs real-time latency

### Observability
- **Sentry**: error tracking + performance traces (orchestrator + GPU server)
- **Better Stack**: structured JSON logs (VPS + GPU + Docker containers)
- **api_usage_logs table**: tracks every external API call with cost estimates
- **user_events table**: unified audit/activity/analytics (AD-021)

---

## Working Protocol

- **Read RULES.md FIRST** at the start of every session
- **Browser-based QA required** — not just API testing. Walk the user journey.
- **Video loading**: 16 at a time, server-side paginated with 24h cache
- **GPU server Python**: use python3/3.10, NOT 3.11
- **Iceland network**: slow. Downloads need `--default-timeout=300 --retries=5`. Use screen sessions.
- **Direct pushback over sycophancy** — say what's wrong, don't sugarcoat
- **Concise analysis** — numbered fixes with file:line refs
- **Test the actual UI**, not just API endpoints
