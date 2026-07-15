# LUMINACAST OMNI — Production Development Brief v4.1
## AI Avatar Live Selling Platform for TikTok Shop Creators
### March 26, 2026 — LLM-Ready Specification (Patched)

---

## LLM CODING DIRECTIVES

This document is the single source of truth for building Luminacast Omni. Follow these rules:

- **Prefer pure functions.** Pass config via env vars, not globals.
- **All external API calls** (OpenRouter, Fish Audio, RunPod, R2, Stripe, Apify) must be wrapped in small adapter modules (e.g. `services/fish_audio.py`) so they can be mocked in tests.
- **Log with structured JSON logs** — fields: `service`, `creator_id`, `cast_id`, `level`, `message`, `timestamp`.
- **Error handling on every external call** — retry with exponential backoff, max 3 retries, then fail gracefully.
- **Type everything** — use Pydantic models for all API request/response schemas, TypeScript interfaces for all frontend data.
- **No magic strings** — use enums for statuses, layout modes, block types.
- **Tests first for core logic** — mashup engine, variant scoring, billing calculations.

---

## TABLE OF CONTENTS

1. Product Overview
2. Repository Structure & Codebase Layout
3. Infrastructure, Tech Stack & Credentials
4. Database Schema (PostgreSQL + Neo4j)
5. Architecture Overview
6. Avatar System (Two Paths)
7. Content Creation Pipeline (The "Cast" System)
8. Streaming Engine & Scene Compositor
9. Template Studio & Mashup Engine
10. Chat System (Dual Persona + Multi-User)
11. Product Pinning & AFK Safety
12. Analytics & Graph Data Intelligence
13. Admin Panel & Monitoring (Sentry + Alerts)
14. Billing Model (Pay-Per-Use)
15. Storage Architecture (Cloudflare R2)
16. Auth Model, Roles & Security
17. Companion App UI/UX Specification
18. API Endpoints
19. WebSocket Event Dictionary
20. RunPod Serverless Contract
21. Error Handling & Retry Policy
22. Rate Limits & Abuse Prevention
23. Legal & Consent
24. Testing Infrastructure & Automation
25. Docker Compose & Deployment
26. Build Plan (4 Weeks)
27. Cost Model & Pricing

---

## 1. PRODUCT OVERVIEW & COMPLETE FUNCTIONALITY

Luminacast Omni is a pay-per-use platform enabling TikTok Shop affiliate creators to generate and stream AI avatar live selling content on their TikTok accounts. The creator's AI avatar — either a photorealistic digital twin of themselves or a fully generated AI character — presents products, responds to chat, and drives sales while the creator monitors from a companion web app.

**No monthly subscription.** Two revenue moments: Cast creation ($14.99 per content package) and streaming ($0.03 per minute while live on TikTok).

---

### 1.1 AVATAR SYSTEM

The platform supports two distinct avatar creation paths. Both produce the same output: a face reference, a cloned voice profile, and a persona description that drives script generation and chat personality.

**Path A — Digital Clone (Creator's Own Face & Voice)**

The creator provides either a TikTok profile/video URL or records directly from their device.

*TikTok URL method:* The system uses Apify TikTok Scraper to download 3-5 recent public videos from the creator's profile. From these videos it extracts: (a) the best face frame for InfiniteTalk reference, (b) audio tracks for Fish Audio S2 voice cloning (10-30 seconds needed), and (c) full transcripts that are analyzed by an LLM to build a "persona profile" — capturing tone, catchphrases, vocabulary level, selling style, emoji usage, pacing, greeting/closing patterns, topics to avoid. This persona profile feeds into both script generation and chat AI personality, ensuring the avatar speaks and behaves like the real creator. If Apify scraping fails (TikTok throttling, private account, etc.), the UI falls back to prompting the creator to upload 3 reference videos manually.

*Record from scratch:* Creator records a 30-60 second reference video (face centered, good lighting, natural talking) and a 10-30 second voice sample (clear speech, minimal background noise). Optionally fills out a style questionnaire to define their persona manually.

**Path B — Fully Digital AI Avatar**

The creator generates or selects a synthetic character. A face image is generated via Nano Banana Pro (OpenRouter), a voice is selected from Fish Audio's 100k+ voice library or cloned from any audio sample, and a persona is defined via presets ("Energetic Beauty Guru", "Calm Tech Reviewer") or custom description. The creator never appears on camera.

**Both paths require explicit consent.** Before any cloning, the creator must check a mandatory checkbox confirming they own the rights to the content and authorize voice/visual cloning.

**Avatar setup is a one-time paid action ($9.99).** The avatar persists across all future Casts.

---

### 1.2 THE "CAST" SYSTEM (Content Package)

A Cast is the core content unit — a complete, ready-to-stream content package containing everything needed for one live stream session. Think of it as a TV show episode: pre-produced, reviewed, approved, then aired.

**Cast creation is a guided, multi-step process:**

*Step 1 — Select Products:* Creator picks 3-8 affiliate products from TikTok Shop. For each product: upload photos (at least 1 required), optionally provide a product demo video or let AI generate one, enter name/price/commission rate/description. The system stores product data and generates transparent overlay images (background-removed product photos for stream compositing).

*Step 2 — Provide Product Media:* For each product, the creator provides or the system generates: product photos, product video (unboxing, demo, usage — uploaded by creator or generated by AI from photos), VFX assets (sparkle overlays, sale banners — from platform's shared VFX library), and composite scene images (avatar + product in the same frame, generated by Nano Banana Pro).

*Step 3 — Review Script Outline:* The system generates a script outline using an LLM (OpenRouter). For each scene/block in the Cast, the outline includes: the mood/atmosphere ("energetic, intimate, urgent"), key selling points to cover, style directives ("show texture close-up", "mention free shipping"), do's and don'ts, and estimated duration. The creator reviews this outline, can edit any part, and approves it before proceeding.

*Step 4 — Generate Full Scripts:* The LLM expands the approved outline into full speech scripts, using the creator's persona profile to match their voice and style. It generates 2-3 script variants per scene for variety — so the stream doesn't sound identical each time it loops. The creator reviews and can edit scripts.

*Step 5 — Choose Template & Layout:* The creator picks from pre-built stream templates (proven live selling formats): "Beauty Haul" (intro → product → social proof → product → flash sale → bundle CTA → closing), "Tech Unbox" (intro → unboxing → specs → demo → versus → verdict CTA → closing), "Flash Marathon" (intro → product → countdown → product → countdown → mega deal → closing), "Lifestyle Show" (intro → mood → product in context → testimonial → product → soft CTA → closing), "Single Product Deep Dive" (intro → story → problem → solution → demo → reviews → offer → closing), or Custom (drag-and-drop any blocks). For each block they assign: which scene image/background, which compositor layout mode (full avatar, split screen, PiP, etc.), which product overlay, and auto-basket timeout settings.

*Step 6 — Preview & Pay:* The system shows a preview: estimated stream cycle duration, number of scenes, number of clips to generate, estimated generation time (15-30 minutes), and cost ($14.99). Creator pays via Stripe. Generation begins as a background Celery task.

*Step 7 — Background Generation:* The pipeline runs on RunPod Serverless: Fish Audio S2 generates TTS audio for all script variants, InfiniteTalk generates avatar video for each audio + scene combination, FFmpeg composites product overlays into each clip. All results upload to Cloudflare R2. Creator gets notified when complete. If more than 30% of clips fail generation, the Cast is marked as failed and the creator is notified to retry.

*Step 8 — Review & Approve:* Creator watches previews of all generated clips in the companion app. Can reject individual clips and regenerate them ($0.99 per clip). Approves Cast — status changes to "ready."

*Step 9 — Schedule & Stream:* Creator schedules the Cast for a specific date/time/timezone, sets maximum duration (up to 4 hours), and whether it should loop. At the scheduled time, the streaming engine loads the Cast and begins the RTMP broadcast to TikTok. Per-minute streaming charges begin.

---

### 1.3 STREAMING ENGINE

The streaming engine sequences pre-generated Cast clips into a continuous RTMP feed pushed to TikTok Live. It runs as a Celery background task managing an FFmpeg subprocess.

**Clip sequencing:** The orchestrator follows the Cast's template block order (or shuffle mode), picking from available variants using weighted randomness. The mashup engine ensures: no immediate variant repeats, fair product rotation (each product gets equal airtime), and scene variety (doesn't stay in the same background for more than 2 consecutive blocks). When all blocks have played, the sequence loops from the beginning.

**Scene compositor:** FFmpeg applies real-time overlays based on the current layout mode. Six layout presets: (1) full_avatar — avatar fills the 720×1280 frame, used for intros/closings/chat moments; (2) avatar_product — avatar full frame with product card overlay at bottom-left, the default for product pitches; (3) split_screen — avatar left half, product right half, for product details; (4) pip_product — product fills frame with small avatar in corner, for product showcases; (5) product_only — product image with price and CTA text, no avatar, for flash sale countdowns; (6) auto_basket — current layout plus an animated "Tap basket to buy!" banner, triggered when the creator hasn't pinned the product.

**Overlay switching:** Between blocks, the orchestrator swaps overlay files (product images, price text, CTA banners) on disk. FFmpeg's `drawtext` with `reload=1` re-reads text each frame; product images are swapped via symlinks.

**Output specs:** H.264 Baseline profile, 720×1280 at 30fps, 2500kbps video, AAC 44100Hz stereo 128kbps audio, keyframes every 2 seconds, FLV container via FIFO muxer to TikTok's RTMP endpoint. The FIFO muxer handles automatic reconnection on network drops.

**Idle handling:** When the clip queue is empty or the stream is paused, a pre-rendered idle loop plays (avatar in neutral pose, gentle ambient movement) until content resumes.

---

### 1.4 TEMPLATE STUDIO & MASHUP ENGINE

Templates are structured block sequences defining the flow of a live stream. Each template is a proven live selling format. Creators select a template during Cast creation and can customize block order, add/remove blocks, or build entirely custom sequences.

**Block types:** intro (avatar greets viewers, teases products), product (avatar pitches a specific product with overlay), flash_sale (countdown timer with urgency CTA), social_proof (avatar reads reviews or shows testimonials), cta (call to action — "buy now" with bundle deal overlay), filler (transition content, shipping reminders, engagement prompts), idle (silent ambient loop for gaps), closing (avatar signs off, thanks viewers, promotes next stream).

**Mashup engine:** During streaming, the engine creates variety by combining scene images, layout modes, script variants, and product media in different combinations. It tracks which variant was playing when each purchase occurred, building a performance score over time (purchases_during / times_played). Higher-performing variants are weighted to play more often. This creates an automatic A/B testing system — the best-converting content gets more airtime.

---

### 1.5 CHAT SYSTEM (Dual Persona + Multi-User)

The chat system monitors TikTok Live chat in real-time via TikTok-Live-Connector (Node.js) and responds through a three-tier pipeline.

**Tier 1 — Pre-recorded response clips (<1 second):** Keyword matching detects common questions (shipping, price, ingredients, sizing). Matched keywords trigger a pre-recorded video clip of the avatar answering that specific question, injected into the stream queue. A text version of the answer is simultaneously posted in TikTok chat.

**Tier 2 — Product-specific Q&A rules (<1 second):** Per-product Q&A rules defined during Cast creation. When a viewer asks about a product, the system checks the rules and posts a text reply. Example: product "Summer Glow Serum" has rule: keywords ["oily", "skin type"] → "Yes! Our serum is oil-free and controls excess sebum."

**Tier 3 — LLM fallback (1-3 seconds):** Questions not matched by Tier 1 or 2 are sent to an LLM (OpenRouter — Llama 3 70B or Gemini Flash). The LLM receives context: current product info, creator's persona profile, last 20 chat messages, and safety constraints. Response is either auto-sent or queued for creator approval depending on settings.

**Two chat personas post from the same TikTok account:** (1) AI Assistant — fully automatic, handles ~80% of questions. (2) Creator/Team — manual messages typed by the creator or their team member in the companion app. The companion app visually distinguishes AI-sent vs. human-sent messages.

**Multi-user support:** Up to 2 concurrent chat operators (creator + 1 invited team member). Both see the same chat feed and AI drafts. Collision prevention: when one operator clicks "Edit" on a draft, it locks for that operator — the other sees "Being edited by [name]." If neither responds within 15 seconds and auto-send is enabled, the AI response sends automatically.

**Creator takeover:** The creator can type a message in the companion app. In "chat only" mode, it posts as text to TikTok chat instantly. In "voice + chat" mode, the text is sent to Fish Audio TTS → InfiniteTalk generates a quick clip → the clip is injected into the stream as priority content (15-30 second delay).

---

### 1.6 PRODUCT PINNING & AFK SAFETY

TikTok does NOT have an API for product pinning. The creator (or team member) must manually pin products on TikTok's Streamer Desktop app. Luminacast solves this with a co-pilot notification system and an AFK safety net.

**When creator is online:** Each time the stream transitions to a product block, the companion app shows a prominent alert: "📌 PIN: Summer Glow Serum — NOW" with a countdown timer. The creator pins the product on Streamer Desktop and taps "Pinned ✓" in the companion app to cancel the timer.

**When creator is AFK (auto-basket system):** If the creator doesn't confirm the pin within the timeout (configurable per block, default 60 seconds): (a) an animated "🛒 Tap the basket below to buy!" overlay appears on the stream, (b) the chat bot posts a purchase reminder in TikTok chat with the product name and price, (c) the event is logged for analytics. The stream continues playing — it doesn't stop.

**Escalation:** 1 AFK event = overlay + chat CTA only. 3 consecutive = push notification to creator's phone (future feature). 5 consecutive = companion app suggests pausing. 10+ = auto-switch to idle loop (no product blocks until creator returns).

---

### 1.7 COMPANION APP (Co-Pilot)

A React web application the creator uses alongside TikTok's Streamer Desktop during live streams, and for all pre-stream content management.

**Dashboard (home screen):** Overview of creator's account — active Casts, upcoming scheduled streams, quick stats (earnings this week, total purchases, stream hours), recent performance chart.

**Cast Builder:** Step-by-step guided Cast creation flow (described in 1.2). Product entry, script outline review/edit, full script review/edit, template selection with drag-and-drop block reordering, payment, generation progress tracking, clip preview and approval.

**Live Control (cockpit):** Real-time stream monitoring during broadcast. Shows: low-res stream preview, NOW PLAYING bar (current block, product, time remaining, next-up), skip/pause/previous controls, script timeline with block progress indicators, chat panel with incoming messages + AI drafts + purchase events, creator takeover input (text → chat or text → voice + chat), product pin confirmation button with countdown timer, auto-basket warning, and quick stats (duration, viewers, purchases, estimated commissions). Also shows which operators are currently online.

**Setup:** One-time configuration. Avatar creation (clone or digital), voice setup with test TTS preview, TikTok RTMP stream key input (per session — must be pasted from livecenter.tiktok.com), team member invitation, billing history via Stripe Customer Portal.

**Analytics:** Per-Cast performance, per-variant conversion scores, product leaderboard, time-of-day analysis, chat insights (most common questions, AI response accuracy), AFK event history.

**Admin Panel (admin role only):** All-creators dashboard, active streams monitoring, server health (CPU/RAM/disk/bandwidth), alert feed, revenue tracking, platform-wide product intelligence.

---

### 1.8 ANALYTICS & DATA INTELLIGENCE

Every stream session generates detailed analytics stored in both PostgreSQL (transactional, for billing and simple queries) and Neo4j (graph, for relationship discovery and trend intelligence).

**Per-stream metrics:** Duration, peak/average viewers, total purchases, total GMV, per-product breakdown (units sold, GMV, best-performing variant, average time-to-purchase), per-variant performance scores, chat statistics (total messages, AI-responded, creator-responded, unanswered, top questions), AFK events, minute-by-minute engagement timeline.

**Per-variant performance scoring:** After each session, the system calculates purchases_during / times_played for each variant. Over time this builds a reliable conversion score. The mashup engine uses these scores to weight future variant selection — best performers play more often, automatically optimizing stream content.

**Platform-wide intelligence (Neo4j graph):** All stream data across all creators feeds into a graph database. This enables queries like: which products are trending this week, which products are frequently bought together, which selling techniques (layout mode, script style, scene type) correlate with higher conversion, what price points convert best in live streams, what time-of-day patterns exist. This becomes a competitive moat — Luminacast knows which products sell well in live streams before individual creators do.

---

### 1.9 ADMIN PANEL & MONITORING

**Dashboard:** Total active streams, total creators (active/inactive), revenue today/week/month, server health metrics, alert feed.

**Server monitoring with threshold alerts:** CPU > 80% sustained 5 min = warning; > 95% sustained 1 min = critical. RAM > 85% = warning; > 95% = critical. Disk > 80% = warning; > 95% = critical + auto-cleanup. Concurrent streams > 3 on KVM 4 = capacity warning; > 4 = scale-up needed. FFmpeg crash = critical + auto-restart. RTMP disconnect = auto-reconnect. RunPod queue > 20 jobs = generation backlog. Stripe payment failure = billing issue.

**Error tracking:** Sentry integrated in both backend (FastAPI + Celery + SQLAlchemy) and frontend (React). All unhandled exceptions captured with full context (creator_id, cast_id, session_id).

---

### 1.10 BILLING MODEL

**Pay-per-use, no subscription.**

| Item | Price | Covers |
|------|-------|--------|
| **Avatar setup** | $9.99 one-time | Clone from TikTok or generate digital avatar |
| **Cast creation** | $14.99 per Cast | Script generation, TTS audio, avatar video clips, overlays |
| **Streaming** | $0.03 per minute ($1.80/hr) | Live RTMP broadcast + chat AI + real-time monitoring |
| **Clip regeneration** | $0.99 per clip | Redo a bad clip within a Cast |

**Creator economics:** A creator streaming 3 hours/day, 5 days/week, with 4 new Casts per month spends ~$168/month on Luminacast. At $25/hour in affiliate commissions (60 streaming hours × $25 = $1,500/month), Luminacast costs 11.2% of earnings — dramatically cheaper than a human host at $2,000-5,000/month. The ROI is ~9×.

**Stripe implementation:** Cast creation and avatar setup are one-time PaymentIntents. Streaming is metered billing — usage (minutes) is reported to Stripe at end of each stream session. Clip regeneration is an additional one-time charge.

---

## 2. REPOSITORY STRUCTURE & CODEBASE LAYOUT

### GitHub
- **Account:** 3gorka72@gmail.com
- **Repository:** `luminacast-omni` (new, clean — delete any previous Luminacast repos)
- **Branch strategy:** `main` (production), `develop` (working branch), feature branches `feat/xxx`

### Monorepo Structure

```
luminacast-omni/
├── README.md
├── .env.example                    # Template — NEVER commit real .env
├── .gitignore
├── docker-compose.yml
├── docker-compose.staging.yml
│
├── backend/
│   ├── orchestrator/               # FastAPI — Python 3.12
│   │   ├── main.py                 # FastAPI app entry point
│   │   ├── config.py               # Pydantic Settings (reads .env)
│   │   ├── database.py             # SQLAlchemy engine + session
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── avatar.py
│   │   │   ├── cast.py
│   │   │   ├── product.py
│   │   │   ├── block.py
│   │   │   ├── stream_session.py
│   │   │   ├── chat_message.py
│   │   │   └── billing_event.py
│   │   ├── schemas/                # Pydantic request/response schemas
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── cast.py
│   │   │   ├── stream.py
│   │   │   ├── chat.py
│   │   │   └── analytics.py
│   │   ├── routers/                # FastAPI route handlers
│   │   │   ├── auth.py
│   │   │   ├── avatar.py
│   │   │   ├── casts.py
│   │   │   ├── stream.py
│   │   │   ├── chat.py
│   │   │   ├── products.py
│   │   │   ├── analytics.py
│   │   │   └── admin.py
│   │   ├── services/               # External API adapters (mockable)
│   │   │   ├── openrouter.py       # LLM + image generation
│   │   │   ├── fish_audio.py       # TTS + voice cloning
│   │   │   ├── runpod.py           # InfiniteTalk job submission
│   │   │   ├── r2_storage.py       # Cloudflare R2 (boto3)
│   │   │   ├── stripe_billing.py   # Stripe payments + webhooks
│   │   │   ├── apify_tiktok.py     # TikTok scraping via Apify
│   │   │   └── sentry.py           # Sentry error tracking
│   │   ├── engine/                 # Core business logic
│   │   │   ├── mashup.py           # Variant selection + weighting
│   │   │   ├── compositor.py       # FFmpeg filter graph builder
│   │   │   ├── streamer.py         # RTMP streaming process manager
│   │   │   ├── cast_generator.py   # Cast generation pipeline
│   │   │   ├── chat_classifier.py  # Keyword + LLM chat routing
│   │   │   └── afk_monitor.py      # Auto-basket timer logic
│   │   ├── tasks/                  # Celery background tasks
│   │   │   ├── __init__.py
│   │   │   ├── generate_cast.py    # Async Cast generation
│   │   │   ├── generate_avatar.py  # Async avatar cloning
│   │   │   └── stream_worker.py    # FFmpeg subprocess manager
│   │   ├── websocket/              # WebSocket handlers
│   │   │   ├── manager.py          # Connection manager
│   │   │   └── events.py           # Event type definitions
│   │   ├── migrations/             # Alembic migrations
│   │   │   └── versions/
│   │   ├── alembic.ini
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── tests/
│   │       ├── test_mashup.py
│   │       ├── test_billing.py
│   │       ├── test_chat_classifier.py
│   │       └── test_cast_generator.py
│   │
│   └── chat-monitor/              # Node.js 20 — TikTok chat bridge
│       ├── index.js               # TikTok-Live-Connector → WebSocket relay
│       ├── package.json
│       ├── Dockerfile
│       └── .env.example
│
├── frontend/
│   ├── companion-app/             # React 18 (Vite) + TypeScript
│   │   ├── src/
│   │   │   ├── App.tsx
│   │   │   ├── main.tsx
│   │   │   ├── stores/            # Zustand stores
│   │   │   │   ├── streamStore.ts # Live stream state
│   │   │   │   ├── chatStore.ts   # Chat messages + drafts
│   │   │   │   └── authStore.ts   # JWT + user info
│   │   │   ├── hooks/
│   │   │   │   ├── useWebSocket.ts
│   │   │   │   └── useStreamTimer.ts
│   │   │   ├── pages/
│   │   │   │   ├── Dashboard.tsx
│   │   │   │   ├── CastBuilder.tsx
│   │   │   │   ├── LiveControl.tsx
│   │   │   │   ├── Setup.tsx
│   │   │   │   ├── Analytics.tsx
│   │   │   │   └── Admin.tsx      # Admin panel (role-gated)
│   │   │   ├── components/
│   │   │   │   ├── NowPlaying.tsx
│   │   │   │   ├── ChatPanel.tsx
│   │   │   │   ├── ScriptTimeline.tsx
│   │   │   │   ├── CastPreview.tsx
│   │   │   │   └── ...
│   │   │   ├── lib/
│   │   │   │   ├── api.ts         # REST client (axios + React Query)
│   │   │   │   └── types.ts       # TypeScript interfaces
│   │   │   └── styles/
│   │   │       └── globals.css    # Tailwind + shadcn/ui theme
│   │   ├── index.html
│   │   ├── vite.config.ts
│   │   ├── tailwind.config.ts
│   │   ├── tsconfig.json
│   │   ├── package.json
│   │   ├── Dockerfile
│   │   └── tests/
│   │       └── e2e/               # Playwright E2E tests
│   │           ├── onboarding.spec.ts
│   │           ├── cast-builder.spec.ts
│   │           └── live-control.spec.ts
│
├── runpod/
│   ├── handler.py                 # RunPod serverless handler
│   ├── Dockerfile                 # InfiniteTalk + Wan 2.1
│   └── requirements.txt
│
└── infra/
    ├── nginx/
    │   └── luminacast.conf        # Reverse proxy config
    ├── sentry/
    │   └── sentry.properties
    └── scripts/
        ├── setup-server.sh        # Initial VPS provisioning
        ├── deploy.sh              # Pull + rebuild + restart
        └── backup-db.sh           # pg_dump to R2
```

---

## 3. INFRASTRUCTURE, TECH STACK & CREDENTIALS

### Full Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Backend API** | FastAPI | latest (Python 3.12) |
| **ORM** | SQLAlchemy 2.0 + Alembic | latest |
| **Task Queue** | Redis + Celery | Redis 7, Celery 5 |
| **Database (relational)** | PostgreSQL 16 | via Docker on VPS |
| **Database (graph)** | Neo4j Community | via Docker on VPS |
| **Chat Bridge** | Node.js + tiktok-live-connector | Node 20 LTS |
| **Frontend** | React 18 + TypeScript + Vite | latest |
| **UI Library** | shadcn/ui (Radix UI + Tailwind CSS) | latest |
| **State Management** | Zustand | latest |
| **Data Fetching** | @tanstack/react-query | v5 |
| **Video Processing** | ffmpeg-python wrapper | latest |
| **TikTok Scraping** | Apify TikTok Scraper | via API |
| **Error Tracking** | Sentry (Python + React SDKs) | latest |
| **E2E Testing** | Playwright | latest |
| **Unit Testing** | pytest (Python), Vitest (React) | latest |
| **Containerization** | Docker + Docker Compose | latest |
| **Reverse Proxy** | nginx | latest |

### Environment Variables (.env)

```bash
# === Server ===
APP_ENV=production           # production | staging | development
APP_SECRET_KEY=              # openssl rand -hex 32
APP_DOMAIN=luminacast.com
ALLOWED_ORIGINS=https://luminacast.com,https://app.luminacast.com

# === Database ===
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=luminacast
POSTGRES_USER=luminacast
POSTGRES_PASSWORD=            # generate strong password
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=               # generate strong password

# === Redis ===
REDIS_URL=redis://localhost:6379/0

# === OpenRouter ===
OPENROUTER_API_KEY=           # sk-or-v1-xxxx

# === Fish Audio ===
FISH_AUDIO_API_KEY=           # xxxx

# === RunPod ===
RUNPOD_API_KEY=               # rpa_xxxx
RUNPOD_VOLUME_ID=             # vol_xxxx
RUNPOD_ENDPOINT_ID=           # will be set after container deployment

# === Cloudflare R2 ===
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_ENDPOINT=                  # https://{account_id}.r2.cloudflarestorage.com
R2_BUCKET=luminacast

# === Stripe ===
STRIPE_PUBLISHABLE_KEY=       # pk_test_xxxx or pk_live_xxxx
STRIPE_SECRET_KEY=            # sk_test_xxxx or sk_live_xxxx
STRIPE_WEBHOOK_SECRET=        # whsec_xxxx

# === Apify ===
APIFY_API_TOKEN=              # apify_api_xxxx

# === Sentry ===
SENTRY_DSN_BACKEND=           # https://xxx@sentry.io/xxx
SENTRY_DSN_FRONTEND=          # https://yyy@sentry.io/yyy

# === Internal Service Auth ===
INTERNAL_SERVICE_SECRET=      # openssl rand -hex 32 — used by chat-monitor to authenticate with orchestrator

# === TikTok Chat Monitor ===
TIKTOK_SESSION_ID=            # optional, for authenticated chat features
ORCHESTRATOR_WS_URL=ws://localhost:8000/ws/internal/chat
```

**CRITICAL:** Never commit `.env` files. The `.env.example` contains keys with empty values as a template.

---

## 4. DATABASE SCHEMA

### PostgreSQL (Relational — application state)

PostgreSQL stores all structured application data. Full manifests and media live in R2. DB holds indexed metadata and references.

```python
# backend/orchestrator/models/user.py
from sqlalchemy import Column, String, DateTime, Enum, Boolean, JSON
from sqlalchemy.orm import relationship
import enum

class UserRole(str, enum.Enum):
    CREATOR = "creator"
    OPERATOR = "operator"  # team member with chat-only access
    ADMIN = "admin"

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)           # prefix: usr_
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.CREATOR)
    stripe_customer_id = Column(String, unique=True)
    tiktok_handle = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, default=True)
    
    avatars = relationship("Avatar", back_populates="user")
    casts = relationship("Cast", back_populates="user")
    team_members = relationship("TeamMember", back_populates="owner")


# backend/orchestrator/models/team_member.py
class TeamMember(Base):
    __tablename__ = "team_members"
    id = Column(String, primary_key=True)           # prefix: tm_
    owner_id = Column(String, ForeignKey("users.id"))
    user_id = Column(String, ForeignKey("users.id"))
    role = Column(String, default="chat_operator")   # chat_operator only for MVP
    invited_at = Column(DateTime, server_default=func.now())
    
    owner = relationship("User", foreign_keys=[owner_id])


# backend/orchestrator/models/avatar.py
class AvatarType(str, enum.Enum):
    CLONE = "clone"
    DIGITAL = "digital"

class AvatarStatus(str, enum.Enum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

class Avatar(Base):
    __tablename__ = "avatars"
    id = Column(String, primary_key=True)           # prefix: avt_
    user_id = Column(String, ForeignKey("users.id"))
    type = Column(Enum(AvatarType))
    status = Column(Enum(AvatarStatus), default=AvatarStatus.PROCESSING)
    face_ref_key = Column(String)                    # R2 key
    video_ref_key = Column(String, nullable=True)    # R2 key (clone only)
    voice_id = Column(String)                        # Fish Audio voice profile ID
    persona_profile = Column(JSON)                   # LLM-analyzed style
    tiktok_source_url = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    
    user = relationship("User", back_populates="avatars")


# backend/orchestrator/models/product.py
class Product(Base):
    __tablename__ = "products"
    id = Column(String, primary_key=True)           # prefix: prod_
    user_id = Column(String, ForeignKey("users.id"))
    tiktok_product_url = Column(String, nullable=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    commission_rate = Column(Float, default=0.15)
    description = Column(String, nullable=True)
    media_keys = Column(JSON)                        # [r2_key, r2_key, ...]
    overlay_key = Column(String, nullable=True)      # R2 key for transparent overlay
    created_at = Column(DateTime, server_default=func.now())


# backend/orchestrator/models/cast.py
class CastStatus(str, enum.Enum):
    DRAFT = "draft"
    OUTLINE_REVIEW = "outline_review"
    SCRIPT_REVIEW = "script_review"
    TEMPLATE_SELECT = "template_select"
    PENDING_PAYMENT = "pending_payment"
    GENERATING = "generating"
    GENERATION_FAILED = "generation_failed"
    READY = "ready"
    SCHEDULED = "scheduled"
    LIVE = "live"
    COMPLETED = "completed"

class Cast(Base):
    __tablename__ = "casts"
    id = Column(String, primary_key=True)           # prefix: cst_
    user_id = Column(String, ForeignKey("users.id"))
    avatar_id = Column(String, ForeignKey("avatars.id"))
    name = Column(String)
    status = Column(Enum(CastStatus), default=CastStatus.DRAFT)
    template_name = Column(String, nullable=True)
    schedule_at = Column(DateTime, nullable=True)
    schedule_timezone = Column(String, nullable=True)
    max_duration_minutes = Column(Integer, default=240)
    loop = Column(Boolean, default=True)
    r2_manifest_key = Column(String, nullable=True)  # R2 key for full JSON manifest
    creation_fee_cents = Column(Integer, nullable=True)
    creation_paid = Column(Boolean, default=False)
    generation_progress = Column(Float, default=0.0) # 0.0 to 1.0
    generation_error = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    
    user = relationship("User", back_populates="casts")
    blocks = relationship("Block", back_populates="cast", order_by="Block.position")
    products = relationship("CastProduct", back_populates="cast")
    stream_sessions = relationship("StreamSession", back_populates="cast")


# backend/orchestrator/models/block.py
class BlockType(str, enum.Enum):
    INTRO = "intro"
    PRODUCT = "product"
    FLASH_SALE = "flash_sale"
    SOCIAL_PROOF = "social_proof"
    CTA = "cta"
    FILLER = "filler"
    IDLE = "idle"
    CLOSING = "closing"

class LayoutMode(str, enum.Enum):
    FULL_AVATAR = "full_avatar"
    AVATAR_PRODUCT = "avatar_product"
    SPLIT_SCREEN = "split_screen"
    PIP_PRODUCT = "pip_product"
    PRODUCT_ONLY = "product_only"
    AUTO_BASKET = "auto_basket"

class Block(Base):
    __tablename__ = "blocks"
    id = Column(String, primary_key=True)           # prefix: blk_
    cast_id = Column(String, ForeignKey("casts.id"))
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    type = Column(Enum(BlockType))
    position = Column(Integer)                       # order in sequence
    layout_mode = Column(Enum(LayoutMode), default=LayoutMode.FULL_AVATAR)
    scene_image_key = Column(String, nullable=True)  # R2 key
    auto_basket_enabled = Column(Boolean, default=False)
    auto_basket_timeout = Column(Integer, default=60) # seconds
    mood = Column(String, nullable=True)             # "energetic", "intimate"
    key_points = Column(JSON, nullable=True)
    chat_rules = Column(JSON, nullable=True)          # per-block Q&A rules
    
    cast = relationship("Cast", back_populates="blocks")
    variants = relationship("Variant", back_populates="block")


# backend/orchestrator/models/variant.py
class VariantStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"

class Variant(Base):
    __tablename__ = "variants"
    id = Column(String, primary_key=True)           # prefix: var_
    block_id = Column(String, ForeignKey("blocks.id"))
    status = Column(Enum(VariantStatus), default=VariantStatus.PENDING)
    script_text = Column(String)
    audio_key = Column(String, nullable=True)        # R2 key
    video_key = Column(String, nullable=True)        # R2 key
    duration_seconds = Column(Float, nullable=True)
    weight = Column(Float, default=1.0)
    times_played = Column(Integer, default=0)
    purchases_during = Column(Integer, default=0)
    performance_score = Column(Float, nullable=True) # purchases_during / times_played
    generation_error = Column(String, nullable=True)
    retry_count = Column(Integer, default=0)
    
    block = relationship("Block", back_populates="variants")


# backend/orchestrator/models/stream_session.py
class StreamSession(Base):
    __tablename__ = "stream_sessions"
    id = Column(String, primary_key=True)           # prefix: ses_
    cast_id = Column(String, ForeignKey("casts.id"))
    user_id = Column(String, ForeignKey("users.id"))
    started_at = Column(DateTime)
    ended_at = Column(DateTime, nullable=True)
    duration_minutes = Column(Float, default=0)
    peak_viewers = Column(Integer, default=0)
    total_purchases = Column(Integer, default=0)
    total_gmv = Column(Float, default=0)
    streaming_cost_cents = Column(Integer, default=0)
    analytics_r2_key = Column(String, nullable=True) # R2 key for full JSON
    afk_events = Column(Integer, default=0)
    
    cast = relationship("Cast", back_populates="stream_sessions")

    # CONSTRAINT: only 1 active session per cast_id at a time
    __table_args__ = (
        UniqueConstraint('cast_id', name='uq_one_active_session_per_cast',
                         postgresql_where=text("ended_at IS NULL")),
    )


# backend/orchestrator/models/chat_message.py
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True)           # prefix: msg_
    session_id = Column(String, ForeignKey("stream_sessions.id"))
    viewer_username = Column(String)
    message_text = Column(String)
    is_purchase = Column(Boolean, default=False)
    product_id = Column(String, nullable=True)
    ai_draft = Column(String, nullable=True)
    ai_draft_status = Column(String, default="pending") # pending | approved | rejected | auto_sent
    responded_by = Column(String, nullable=True)     # user_id of operator or "ai"
    response_text = Column(String, nullable=True)
    locked_by = Column(String, nullable=True)        # user_id if being edited
    timestamp = Column(DateTime, server_default=func.now())


# backend/orchestrator/models/billing_event.py
class BillingEventType(str, enum.Enum):
    AVATAR_SETUP = "avatar_setup"
    CAST_CREATION = "cast_creation"
    STREAMING_USAGE = "streaming_usage"
    CLIP_REGENERATION = "clip_regeneration"

class BillingEvent(Base):
    __tablename__ = "billing_events"
    id = Column(String, primary_key=True)           # prefix: bill_
    user_id = Column(String, ForeignKey("users.id"))
    type = Column(Enum(BillingEventType))
    amount_cents = Column(Integer)
    stripe_payment_intent_id = Column(String, nullable=True)
    related_id = Column(String, nullable=True)       # cast_id or session_id
    created_at = Column(DateTime, server_default=func.now())
```

### Neo4j (Graph — analytics & intelligence)

Neo4j stores the relationship graph between products, streams, sales events, and trends. This enables queries like "which products sell well together" and "which selling techniques work for this product category."

```cypher
// Node types
(:Creator {id, handle, category})
(:Product {id, name, price, category, tiktok_url})
(:Cast {id, template, created_at})
(:StreamSession {id, started_at, duration, total_gmv, peak_viewers})
(:Variant {id, script_text, performance_score, layout_mode})
(:PurchaseEvent {id, timestamp, amount, viewer_username})
(:ChatQuestion {text, frequency})

// Relationships
(:Creator)-[:OWNS]->(:Cast)
(:Cast)-[:FEATURES]->(:Product)
(:Cast)-[:STREAMED_AS]->(:StreamSession)
(:StreamSession)-[:PLAYED]->(:Variant)
(:Variant)-[:PITCHED]->(:Product)
(:PurchaseEvent)-[:DURING]->(:StreamSession)
(:PurchaseEvent)-[:BOUGHT]->(:Product)
(:PurchaseEvent)-[:TRIGGERED_BY]->(:Variant)
(:Product)-[:OFTEN_BOUGHT_WITH]->(:Product)  // computed
(:Product)-[:TRENDING_IN]->(:Category)        // computed
(:ChatQuestion)-[:ABOUT]->(:Product)

// Example intelligence queries:
// "Which products sell best when pitched with split_screen layout?"
MATCH (v:Variant {layout_mode: 'split_screen'})-[:PITCHED]->(p:Product),
      (pe:PurchaseEvent)-[:TRIGGERED_BY]->(v)
RETURN p.name, COUNT(pe) AS sales ORDER BY sales DESC

// "Which products are frequently bought together?"
MATCH (pe1:PurchaseEvent)-[:BOUGHT]->(p1:Product),
      (pe2:PurchaseEvent)-[:BOUGHT]->(p2:Product)
WHERE pe1.viewer_username = pe2.viewer_username AND p1 <> p2
RETURN p1.name, p2.name, COUNT(*) AS co_purchases ORDER BY co_purchases DESC
```

### Data Flow: PostgreSQL vs Neo4j vs R2

| Data type | Where | Why |
|-----------|-------|-----|
| User accounts, auth, billing | PostgreSQL | Transactional, needs ACID |
| Cast metadata, block structure | PostgreSQL | Relational queries, status tracking |
| Stream session metadata | PostgreSQL | Billing calculations, simple queries |
| Chat messages (current session) | PostgreSQL | Fast read/write during live stream |
| Full Cast manifest (clips, scripts) | R2 (JSON) | Large, versioned, shared across services |
| Media files (video, audio, images) | R2 | Binary blobs, served to FFmpeg |
| Product-sales relationships | Neo4j | Graph traversal for recommendations |
| Variant performance correlations | Neo4j | Pattern discovery across creators |
| Platform-wide trend data | Neo4j | Competitive intelligence queries |
| Per-session detailed timeline | R2 (JSON) | Large, append-only, analyzed offline |

---

## 5. ARCHITECTURE OVERVIEW

```
┌──────────────────────────────────────────────────────────────┐
│                     CREATOR'S DEVICES                        │
│  Companion App (React)         TikTok Streamer Desktop       │
│       │ WS + REST                    │ Manual product pins   │
└───────┼──────────────────────────────┼───────────────────────┘
        ▼                              │
┌──────────────────────────────────────────────────────────────┐
│                    NGINX REVERSE PROXY                        │
│  /api/*  → FastAPI :8000                                     │
│  /app/*  → React static :3000                                │
│  /ws/*   → WebSocket upgrade :8000                           │
└──────────────────────────────────────────────────────────────┘
        │
┌──────────────────────────────────────────────────────────────┐
│              HOSTINGER VPS (KVM 4 — Docker Compose)          │
│                                                              │
│  ┌─────────────┐ ┌──────────┐ ┌────────────┐ ┌───────────┐ │
│  │ FastAPI      │ │ Celery   │ │ Node.js    │ │ Redis     │ │
│  │ Orchestrator │ │ Workers  │ │ Chat       │ │           │ │
│  │ :8000       │ │          │ │ Monitor    │ │ Task queue│ │
│  └──────┬──────┘ └────┬─────┘ └─────┬──────┘ │ + pub/sub │ │
│         │             │              │        └───────────┘ │
│  ┌──────┴─────────────┴──────────────┴──────────┐          │
│  │              PostgreSQL 16 :5432               │          │
│  │              Neo4j :7687                       │          │
│  └────────────────────────────────────────────────┘          │
│                                                              │
│  ┌────────────────────────────────────────┐                  │
│  │ FFmpeg RTMP Streamer (Celery task)     │                  │
│  │ Managed by Celery — auto-restart on    │                  │
│  │ crash. Reads clips from local R2 cache │──▶ TikTok Live  │
│  └────────────────────────────────────────┘                  │
│                                                              │
│  ┌─────────────┐ ┌─────────────┐                            │
│  │ Sentry SDK  │ │ React App   │                            │
│  │ (backend)   │ │ (nginx      │                            │
│  │             │ │  static)    │                            │
│  └─────────────┘ └─────────────┘                            │
└──────────────────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
┌───────────────┐            ┌──────────────────┐
│ Cloudflare R2 │            │ RunPod Serverless │
│ (all media)   │◄──────────▶│ (InfiniteTalk)   │
└───────────────┘            └──────────────────┘
        │
        ▼
┌───────────────┐
│ Apify Cloud   │
│ (TikTok       │
│  scraping)    │
└───────────────┘
```

---

## 6. AVATAR SYSTEM (Two Paths)

### Path A: Digital Clone

**Option 1 — Clone from TikTok URL:**

Creator provides a TikTok profile URL or specific video URL. System uses **Apify TikTok Scraper** to reliably fetch content.

```python
# services/apify_tiktok.py
import httpx

async def fetch_tiktok_videos(tiktok_url: str, max_videos: int = 5) -> list[dict]:
    """
    Uses Apify TikTok Scraper actor to download videos.
    Returns: [{"video_url": "...", "description": "...", "stats": {...}}]
    Fallback: If Apify fails, return None → UI prompts manual upload.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.apify.com/v2/acts/clockworks~free-tiktok-scraper/runs",
            json={
                "profiles": [tiktok_url],
                "resultsPerPage": max_videos,
                "shouldDownloadVideos": True,
            },
            headers={"Authorization": f"Bearer {settings.APIFY_API_TOKEN}"},
            timeout=120
        )
        # Poll for completion, then download results
        # ... (standard Apify polling pattern)
```

**Persona extraction (after videos downloaded):**

```python
# LLM prompt for persona analysis
PERSONA_EXTRACTION_PROMPT = """
Analyze these TikTok video transcripts and extract the creator's selling persona.

Transcripts:
{transcripts}

Return ONLY valid JSON:
{{
  "tone": "description of speaking tone",
  "energy_level": "low | medium | high",
  "catchphrases": ["phrase1", "phrase2"],
  "vocabulary_level": "casual | professional | gen_z | mixed",
  "selling_style": "description of how they sell",
  "emoji_patterns": ["emoji1", "emoji2"],
  "pacing": "description of speech pacing",
  "dos": ["things this persona does"],
  "donts": ["things this persona avoids"],
  "greeting_style": "how they typically open",
  "closing_style": "how they typically close"
}}
"""
```

**Option 2 — Record from scratch:** Creator uploads reference video (30-60s) + voice sample (10-30s). Fills out optional style questionnaire.

**Fallback chain for TikTok scraping:**
1. Apify TikTok Scraper → success? Use videos.
2. Apify fails? → Show UI: "We couldn't access your TikTok. Please upload 3 reference videos manually."
3. Creator must tick checkbox: **"I confirm I own the rights to this content and authorize voice and visual cloning."**

### Path B: Fully Digital AI Avatar

1. Generate face via OpenRouter → Nano Banana Pro
2. Select voice from Fish Audio library or clone any voice
3. Define persona via presets or custom description

### Both paths produce identical output:
```python
class AvatarOutput(BaseModel):
    avatar_id: str
    type: AvatarType          # clone | digital
    face_ref_key: str         # R2 key
    video_ref_key: str | None # R2 key (clone only)
    voice_id: str             # Fish Audio voice profile
    persona_profile: dict     # LLM-analyzed style
    scene_keys: list[str]     # Generated scene images in R2
```

---

## 7. CONTENT CREATION PIPELINE — THE "CAST" SYSTEM

*(Full Cast creation flow — identical to v3 brief Section 5. The step-by-step journey from product selection → script outline → full scripts → template selection → payment → generation → review → approve → schedule remains unchanged.)*

### Key addition: Generation error handling

```python
# Cast generation error policy
GENERATION_RETRY_MAX = 3
GENERATION_FAILURE_THRESHOLD = 0.3  # 30%

async def generate_cast_clips(cast_id: str):
    cast = await get_cast(cast_id)
    total_variants = sum(len(b.variants) for b in cast.blocks)
    failed_count = 0
    
    for block in cast.blocks:
        for variant in block.variants:
            success = False
            for attempt in range(GENERATION_RETRY_MAX):
                try:
                    audio_key = await fish_audio.generate_tts(
                        text=variant.script_text,
                        voice_id=cast.avatar.voice_id
                    )
                    video_key = await runpod.generate_clip(
                        image_url=r2.get_signed_url(block.scene_image_key),
                        audio_url=r2.get_signed_url(audio_key)
                    )
                    variant.audio_key = audio_key
                    variant.video_key = video_key
                    variant.status = VariantStatus.READY
                    success = True
                    break
                except Exception as e:
                    variant.retry_count = attempt + 1
                    sentry.capture_exception(e)
                    await asyncio.sleep(2 ** attempt)  # exponential backoff
            
            if not success:
                variant.status = VariantStatus.FAILED
                variant.generation_error = str(e)
                failed_count += 1
    
    failure_rate = failed_count / total_variants
    if failure_rate > GENERATION_FAILURE_THRESHOLD:
        cast.status = CastStatus.GENERATION_FAILED
        cast.generation_error = f"{failed_count}/{total_variants} clips failed"
        # Notify creator: "Cast generation failed. Please retry or contact support."
    else:
        cast.status = CastStatus.READY
        # Any individually failed variants marked — creator can regenerate them
```

---

## 8. STREAMING ENGINE & SCENE COMPOSITOR

### FFmpeg Implementation Specifications

- **Library:** Use `ffmpeg-python` wrapper. Do NOT construct raw bash command strings.
- **Resolution strictness:** All clips from RunPod MUST be pre-rendered at exactly `720x1280` (9:16 portrait). No live scaling in the streaming pipeline.
- **Process management:** FFmpeg runs as a **Celery task** (`tasks/stream_worker.py`), NOT in the FastAPI event loop. The task spawns FFmpeg via `asyncio.create_subprocess_exec`. If FFmpeg exits non-zero, Celery auto-restarts it. Sentry captures the error.
- **CRITICAL — Zombie process prevention:** The FFmpeg subprocess MUST be bound to the Celery task lifecycle. Use a `try/finally` block to catch `asyncio.CancelledError` and `KeyboardInterrupt`, and explicitly send `SIGTERM` then `SIGKILL` to the FFmpeg **process group** (not just the PID) to prevent zombie processes when a stream is stopped or Celery restarts.
- **Clip caching:** Before streaming, download next 3 clips from R2 to `/tmp/stream_cache/`. Prefetch while current clip plays. Delete after played.

### FFmpeg Process Lifecycle (MUST follow this pattern)

```python
# tasks/stream_worker.py
import os
import signal
import asyncio
from celery import Task

class StreamTask(Task):
    """Celery task that manages FFmpeg lifecycle and prevents zombies."""
    
    _ffmpeg_process = None
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Cleanup on any failure."""
        self._kill_ffmpeg()
        sentry.capture_exception(exc)
    
    def on_revoke(self, task_id, args, kwargs, **kw):
        """Cleanup when task is revoked (user stops stream)."""
        self._kill_ffmpeg()
    
    def _kill_ffmpeg(self):
        if self._ffmpeg_process and self._ffmpeg_process.returncode is None:
            try:
                # Kill entire process GROUP to catch any child processes
                os.killpg(os.getpgid(self._ffmpeg_process.pid), signal.SIGTERM)
                self._ffmpeg_process.wait(timeout=5)
            except (ProcessLookupError, TimeoutError):
                os.killpg(os.getpgid(self._ffmpeg_process.pid), signal.SIGKILL)
            except Exception:
                pass  # Process already dead

@celery.task(base=StreamTask, bind=True, max_retries=5)
def run_stream(self, session_id: str, cast_id: str, rtmp_url: str):
    try:
        # Start FFmpeg in its own process group
        process = subprocess.Popen(
            ffmpeg_command,
            stdin=subprocess.PIPE,
            preexec_fn=os.setsid  # Create new process group
        )
        self._ffmpeg_process = process
        
        # Feed clips to FFmpeg stdin...
        # (main streaming loop)
        
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        # Task cancelled — clean shutdown
        self._kill_ffmpeg()
        raise
    finally:
        # ALWAYS cleanup, no matter what
        self._kill_ffmpeg()
        # Finalize billing, save analytics
        finalize_stream_session(session_id)
```

### Layout Modes (6 presets — ffmpeg-python filter graphs)

```python
# engine/compositor.py
import ffmpeg

def build_filter_graph(layout: LayoutMode, clip_path: str, overlay_path: str | None):
    """Returns an ffmpeg-python filter graph for the given layout."""
    
    base = ffmpeg.input(clip_path)
    
    if layout == LayoutMode.FULL_AVATAR:
        return base  # no overlay
    
    elif layout == LayoutMode.AVATAR_PRODUCT:
        overlay = ffmpeg.input(overlay_path)
        return ffmpeg.overlay(base, overlay, x="10", y="H-h-10")
    
    elif layout == LayoutMode.SPLIT_SCREEN:
        overlay = ffmpeg.input(overlay_path).filter("scale", 360, 1280)
        left = base.filter("scale", 360, 1280)
        return ffmpeg.filter([left, overlay], "hstack")
    
    # ... etc for each layout mode
```

---

## 9–11. TEMPLATE STUDIO, CHAT SYSTEM, PRODUCT PINNING

*(These sections remain identical to v3 brief. Key additions already incorporated: mashup engine weighting, dual-persona with multi-user collision prevention, AFK timer with escalation levels.)*

---

## 12. ANALYTICS & GRAPH DATA INTELLIGENCE

### Real-time ingestion (during stream)

**Consistency model:** PostgreSQL writes are synchronous (must succeed for billing accuracy). Neo4j writes are dispatched as **Celery background tasks** with their own retry policy. If Neo4j is briefly down, Celery retries until it succeeds — ensuring eventual consistency without blocking the live stream.

```python
# When TikTok-Live-Connector detects a purchase event:
async def on_purchase_event(session_id: str, product_id: str, 
                            viewer: str, amount: float, variant_id: str):
    # 1. Update PostgreSQL (SYNCHRONOUS — must succeed for billing accuracy)
    async with db.session() as session:
        variant = await session.get(Variant, variant_id)
        variant.purchases_during += 1
        variant.performance_score = variant.purchases_during / max(variant.times_played, 1)
        stream = await session.get(StreamSession, session_id)
        stream.total_purchases += 1
        stream.total_gmv += amount
        await session.commit()
    
    # 2. Write to Neo4j (ASYNC via Celery — eventual consistency)
    # If Neo4j is down, Celery retries with exponential backoff
    # PostgreSQL remains the source of truth for billing
    write_purchase_to_graph.delay(
        session_id=session_id,
        product_id=product_id,
        variant_id=variant_id,
        amount=amount,
        viewer=viewer
    )

@celery.task(bind=True, max_retries=10, default_retry_delay=5)
def write_purchase_to_graph(self, session_id, product_id, variant_id, amount, viewer):
    """
    Celery task — writes purchase event to Neo4j graph.
    Retries up to 10 times with exponential backoff if Neo4j is unavailable.
    This ensures eventual consistency: even if Neo4j is down for minutes,
    all purchase events will be written once it recovers.
    """
    try:
        neo4j_driver.execute_query("""
            MERGE (p:Product {id: $product_id})
            MERGE (v:Variant {id: $variant_id})
            MERGE (s:StreamSession {id: $session_id})
            CREATE (pe:PurchaseEvent {
                id: randomUUID(), timestamp: datetime(), 
                amount: $amount, viewer: $viewer
            })
            CREATE (pe)-[:DURING]->(s)
            CREATE (pe)-[:BOUGHT]->(p)
            CREATE (pe)-[:TRIGGERED_BY]->(v)
        """, product_id=product_id, variant_id=variant_id, 
             session_id=session_id, amount=amount, viewer=viewer)
    except Exception as exc:
        sentry.capture_exception(exc)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
```

### Scheduled intelligence (hourly Celery beat)

```python
# Refresh materialized intelligence
@celery.task
def refresh_platform_intelligence():
    # Product trending scores
    neo4j.run("""
        MATCH (pe:PurchaseEvent)-[:BOUGHT]->(p:Product)
        WHERE pe.timestamp > datetime() - duration('P7D')
        WITH p, COUNT(pe) AS sales_7d, SUM(pe.amount) AS gmv_7d
        SET p.trending_score = sales_7d, p.gmv_7d = gmv_7d
    """)
    
    # Co-purchase relationships
    neo4j.run("""
        MATCH (pe1:PurchaseEvent)-[:BOUGHT]->(p1:Product),
              (pe2:PurchaseEvent)-[:BOUGHT]->(p2:Product)
        WHERE pe1.viewer = pe2.viewer AND p1 <> p2
        AND pe1.timestamp > datetime() - duration('P30D')
        MERGE (p1)-[r:OFTEN_BOUGHT_WITH]->(p2)
        SET r.count = COUNT(*)
    """)
```

---

## 13. ADMIN PANEL & MONITORING (Sentry + Alerts)

### Sentry Integration

```python
# backend/orchestrator/services/sentry.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

def init_sentry():
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN_BACKEND,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.2,  # 20% of requests traced
        environment=settings.APP_ENV,
    )
```

```typescript
// frontend/companion-app/src/main.tsx
import * as Sentry from "@sentry/react";

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  integrations: [Sentry.browserTracingIntegration()],
  tracesSampleRate: 0.2,
  environment: import.meta.env.VITE_APP_ENV,
});
```

### Alert System

*(Alert thresholds table identical to v3 brief Section 11. Alerts dispatch to: Sentry alert → admin panel banner → Slack/Discord webhook.)*

---

## 14. BILLING MODEL (Pay-Per-Use)

*(Identical to v3 brief Section 12. Key figures: $14.99/Cast, $0.03/min streaming, $9.99 avatar setup. Creator monthly cost ~$168 against ~$1,500 earnings = 11.2%.)*

---

## 15. STORAGE ARCHITECTURE (Cloudflare R2)

### Media URL Conventions

- **In database and manifests:** Store logical R2 keys (e.g. `creators/usr_001/casts/cst_001/clips/blk_001_var_001.mp4`)
- **Never store full URLs in DB** — the R2 endpoint may change
- **Backend generates signed HTTPS URLs** when serving to frontend: valid for 1 hour
- **Between backend services** (orchestrator → FFmpeg): use direct boto3 download, not signed URLs

```python
# services/r2_storage.py
def get_signed_url(key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for the frontend to access media."""
    return s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.R2_BUCKET, 'Key': key},
        ExpiresIn=expires_in
    )

def upload_file(local_path: str, key: str) -> str:
    """Upload file to R2, return the key."""
    s3_client.upload_file(local_path, settings.R2_BUCKET, key)
    return key

def download_file(key: str, local_path: str) -> str:
    """Download file from R2 to local path."""
    s3_client.download_file(settings.R2_BUCKET, key, local_path)
    return local_path
```

---

## 16. AUTH MODEL, ROLES & SECURITY

### JWT Structure

```python
# Issued by POST /api/auth/login
{
    "sub": "usr_001",           # user ID
    "email": "sarah@example.com",
    "role": "creator",          # creator | operator | admin
    "creator_id": "usr_001",    # for operators: the creator they work for
    "iat": 1711468800,
    "exp": 1711555200           # 24 hour expiry
}
```

### Middleware Rules

```python
# Every API route is protected by role-based middleware

# Creator routes: creator_id from JWT must match resource owner
@router.get("/api/casts")
async def list_casts(user: User = Depends(get_current_user)):
    return await Cast.filter(user_id=user.id)  # user can only see own casts

# Operator routes: can access chat for their assigned creator
@router.post("/api/chat/approve/{msg_id}")
async def approve_chat(user: User = Depends(get_current_user)):
    # user.role must be 'creator' or 'operator'
    # if operator: verify team_member relationship exists

# Admin routes: admin role required
@router.get("/api/admin/dashboard")
async def admin_dashboard(user: User = Depends(require_admin)):
    pass  # only users with role='admin' can access
```

### Internal Service Authentication (Chat Monitor → Orchestrator)

The Node.js chat monitor connects to the FastAPI orchestrator via an internal WebSocket. It does NOT use creator JWTs — it uses a shared service secret.

```python
# websocket/manager.py — internal service auth
async def authenticate_internal_service(token: str) -> bool:
    """Validate the chat-monitor's service token."""
    return token == settings.INTERNAL_SERVICE_SECRET

# On WS connect from chat-monitor:
# ws://localhost:8000/ws/internal/chat?token={INTERNAL_SERVICE_SECRET}
# If token doesn't match → reject connection immediately
```

```javascript
// backend/chat-monitor/index.js — connecting to orchestrator
const ws = new WebSocket(
    `${process.env.ORCHESTRATOR_WS_URL}?token=${process.env.INTERNAL_SERVICE_SECRET}`
);
// This authenticates the chat-monitor as a trusted internal service
// NOT as a creator — it can relay messages for ALL active streams
```

### CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 17. COMPANION APP UI/UX SPECIFICATION

### Technology Stack (Strict)

| Library | Role |
|---------|------|
| **React 18 + TypeScript** (Vite) | Framework |
| **shadcn/ui** (Radix UI + Tailwind) | All UI components — cards, buttons, modals, inputs |
| **Zustand** | Global state: stream state, AFK timer, active operators |
| **@tanstack/react-query v5** | REST API data fetching + caching |
| **Native WebSocket** | Managed via custom `useWebSocket` hook → syncs with Zustand |
| **Sentry React SDK** | Error tracking |
| **Playwright** | E2E testing |
| **Vitest** | Unit testing |

### Design System

```typescript
// lib/theme.ts
export const colors = {
  bg: "#0F0F14",
  surface: "#16161E",
  card: "#1C1C28",
  border: "#2A2A3A",
  accent: "#8B82C0",       // dusty lavender
  green: "#5EA88A",        // success, live, confirmed
  amber: "#B8933A",        // warning, countdown, pending
  red: "#B05656",          // danger, live indicator
  blue: "#7A9DBF",         // info, scenes
  text: "#E8E6F0",
  textDim: "#8B89A0",
  textMuted: "#5A586A",
} as const;
```

### 5 Screens

*(Screen wireframes identical to v3 brief Section 14: Dashboard, Cast Builder, Live Control, Setup, Analytics. Admin panel is a 6th route-gated screen within the same React app.)*

---

## 18. API ENDPOINTS

*(Identical to v3 brief Section 15. All endpoints with their HTTP methods, paths, and descriptions.)*

---

## 19. WEBSOCKET EVENT DICTIONARY

### Connection: `ws://{APP_DOMAIN}/ws/stream/{session_id}`

Auth: JWT token passed as query param `?token=xxx`

### Server → Client Events

```typescript
// Stream state update (every 5 seconds)
{
  type: "STREAM_STATE_UPDATE",
  payload: {
    status: "live" | "paused" | "idle",
    uptime_seconds: number,
    viewers: number,
    total_purchases: number,
    total_gmv: number
  }
}

// Block transition (when clip changes)
{
  type: "BLOCK_TRANSITION",
  payload: {
    current_block_id: string,
    current_block_position: number,
    total_blocks: number,
    block_type: BlockType,
    layout_mode: LayoutMode,
    product_to_pin: { product_id: string, name: string, price: number } | null,
    afk_timer_seconds: number | null,  // null if no product to pin
    next_block_preview: { block_type: string, product_name: string | null }
  }
}

// New chat message with AI draft
{
  type: "NEW_CHAT_MESSAGE",
  payload: {
    message_id: string,
    viewer_username: string,
    message_text: string,
    is_purchase: boolean,
    ai_draft: string | null,
    ai_draft_status: "pending_approval" | "auto_sent" | null
  }
}

// Chat draft lock (another operator is editing)
{
  type: "CHAT_DRAFT_LOCKED",
  payload: {
    message_id: string,
    locked_by: string  // operator name
  }
}

// AFK timer update (every second during countdown)
{
  type: "AFK_TIMER_TICK",
  payload: {
    seconds_remaining: number,
    product_id: string,
    product_name: string
  }
}

// AFK triggered
{
  type: "AFK_TRIGGERED",
  payload: {
    product_id: string,
    overlay_active: true,
    chat_cta_sent: true
  }
}

// Operator presence
{
  type: "OPERATOR_PRESENCE",
  payload: {
    operators: [
      { user_id: string, name: string, status: "online" | "idle" }
    ]
  }
}

// Generation progress (during Cast generation)
{
  type: "GENERATION_PROGRESS",
  payload: {
    cast_id: string,
    progress: number,    // 0.0 to 1.0
    current_step: string, // "Generating audio 5/24" etc.
    failed_count: number
  }
}
```

### Client → Server Events

```typescript
// Confirm product pinned
{
  type: "PIN_CONFIRM",
  payload: {
    product_id: string,
    operator_name: string
  }
}

// Approve AI chat draft
{
  type: "APPROVE_CHAT",
  payload: {
    message_id: string,
    edited_text: string | null  // null = approve as-is
  }
}

// Reject AI chat draft
{
  type: "REJECT_CHAT",
  payload: {
    message_id: string
  }
}

// Lock chat draft for editing
{
  type: "LOCK_CHAT_DRAFT",
  payload: {
    message_id: string
  }
}

// Send manual chat message
{
  type: "SEND_CHAT",
  payload: {
    text: string,
    mode: "chat_only" | "voice_and_chat"
  }
}

// Stream control
{
  type: "STREAM_CONTROL",
  payload: {
    action: "skip" | "previous" | "pause" | "resume"
  }
}
```

---

## 20. RUNPOD SERVERLESS CONTRACT

### Handler Input/Output

```python
# runpod/handler.py
import runpod

def handler(event):
    """
    RunPod Serverless handler for InfiniteTalk clip generation.
    
    Input:
    {
        "input": {
            "image_url": "https://r2.../scene.jpg",      # Presigned R2 URL
            "audio_url": "https://r2.../audio.wav",       # Presigned R2 URL
            "mode": "image_to_video" | "video_to_video",
            "resolution": "480p" | "720p",
            "output_r2_key": "creators/usr_001/casts/cst_001/clips/blk_001_var_001.mp4"
        }
    }
    
    Output (success):
    {
        "output": {
            "video_r2_key": "creators/usr_001/casts/cst_001/clips/blk_001_var_001.mp4",
            "duration_seconds": 45.2,
            "resolution": "720x1280",
            "status": "success"
        }
    }
    
    Output (failure):
    {
        "error": "InfiniteTalk generation failed: CUDA out of memory"
    }
    """
    input_data = event["input"]
    
    # 1. Download image and audio from presigned URLs
    # 2. Run InfiniteTalk inference
    # 3. Upload result to R2 using output_r2_key
    # 4. Return output
    
    return {"output": {...}}

runpod.serverless.start({"handler": handler})
```

### Orchestrator → RunPod Communication

```python
# services/runpod.py
import runpod as runpod_sdk

async def submit_clip_job(image_key: str, audio_key: str, 
                          output_key: str, mode: str = "image_to_video",
                          resolution: str = "720p") -> str:
    """Submit clip generation job. Returns job_id for polling."""
    
    run = runpod_sdk.Serverless.run(
        endpoint_id=settings.RUNPOD_ENDPOINT_ID,
        input={
            "image_url": r2_storage.get_signed_url(image_key, expires_in=7200),
            "audio_url": r2_storage.get_signed_url(audio_key, expires_in=7200),
            "mode": mode,
            "resolution": resolution,
            "output_r2_key": output_key,
        }
    )
    return run["id"]

async def poll_job_status(job_id: str) -> dict:
    """Poll RunPod job status. Returns output when complete."""
    status = runpod_sdk.Serverless.status(
        endpoint_id=settings.RUNPOD_ENDPOINT_ID,
        job_id=job_id
    )
    return status  # {"status": "IN_PROGRESS" | "COMPLETED" | "FAILED", "output": {...}}
```

---

## 21. ERROR HANDLING & RETRY POLICY

### External Service Failures

| Service | On failure | Retry policy | Final fallback |
|---------|-----------|-------------|----------------|
| **Fish Audio TTS** | Single clip audio fails | 3 retries, exponential backoff (2s, 4s, 8s) | Mark variant `FAILED`, allow manual regen |
| **RunPod InfiniteTalk** | Single clip video fails | 3 retries, 10s backoff | Mark variant `FAILED` |
| **OpenRouter LLM** | Script generation fails | 3 retries, 2s backoff | Show error, let creator retry manually |
| **OpenRouter Image** | Scene image fails | 3 retries, 2s backoff | Use placeholder scene |
| **Apify TikTok** | Scraping fails | 1 retry | Prompt creator to upload manually |
| **R2 Upload** | File upload fails | 5 retries, 1s backoff | Sentry alert, block Cast completion |
| **R2 Download** | Clip download fails mid-stream | 3 retries | Skip to next clip, log error |
| **FFmpeg crash** | Process exits non-zero | Auto-restart (Celery), max 5 restarts | Stop stream, notify creator |
| **RTMP disconnect** | TikTok drops connection | FIFO muxer auto-reconnect (built-in) | After 3 failures in 1 min: pause stream |
| **Stripe payment** | Payment fails | Stripe handles retries | Block Cast generation until paid |

### Cast Generation Failure Policy

```
On single variant failure → mark variant status=FAILED, continue others
On >30% variants failed → mark Cast status=GENERATION_FAILED
    → Notify creator: "X of Y clips failed. You can retry failed clips or regenerate."
    → Partial refund option in admin panel
```

### Stream Failure Policy

```
FFmpeg crash → Celery auto-restarts within 5 seconds
    → If >5 crashes in 10 minutes → stop stream, notify creator
    → Sentry captures each crash with full context (cast_id, block_id, clip path)

RTMP disconnect → FIFO muxer retries automatically
    → If disconnected >60 seconds → pause stream (loop idle)
    → Notify creator: "Stream disconnected. Check TikTok stream key."
```

---

## 22. RATE LIMITS & ABUSE PREVENTION

### Per-Creator Limits

| Resource | Limit | Enforced by |
|----------|-------|-------------|
| Max products per Cast | 10 | API validation |
| Max blocks per Cast | 20 | API validation |
| Max variants per block | 5 | API validation |
| Max TTS seconds per Cast | 30 minutes (= ~40 clips × 45s) | API validation |
| Max concurrent generation jobs | 2 Casts generating simultaneously | DB check before submit |
| Max concurrent streams | 1 per creator | DB unique constraint |
| Max Cast creation per day | 5 | API rate limit |
| Max chat messages (manual) per minute | 10 | Rate limiter middleware |
| Max file upload size | 100 MB (video), 20 MB (image), 10 MB (audio) | nginx + API validation |

### API Rate Limits (global)

```python
from slowapi import Limiter
limiter = Limiter(key_func=get_creator_id_from_jwt)

@router.post("/api/casts")
@limiter.limit("5/day")
async def create_cast(...): ...

@router.post("/api/chat/send")
@limiter.limit("10/minute")
async def send_chat(...): ...

@router.post("/api/avatar/clone-from-tiktok")
@limiter.limit("3/day")
async def clone_avatar(...): ...
```

### Input Validation

```python
# All file uploads validated for:
ALLOWED_VIDEO_TYPES = ["video/mp4", "video/webm", "video/quicktime"]
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"]
ALLOWED_AUDIO_TYPES = ["audio/wav", "audio/mpeg", "audio/ogg"]
MAX_VIDEO_SIZE = 100 * 1024 * 1024   # 100 MB
MAX_IMAGE_SIZE = 20 * 1024 * 1024    # 20 MB
MAX_AUDIO_SIZE = 10 * 1024 * 1024    # 10 MB
MAX_SCRIPT_LENGTH = 5000              # characters per variant
```

---

## 23. LEGAL & CONSENT

### Clone Consent (Required)

Before any TikTok cloning or video upload, the creator must:

1. Check a mandatory checkbox: **"I confirm I own the rights to this content and authorize Luminacast to create a digital clone of my voice and likeness for the purpose of generating live stream content."**
2. This consent is stored as `consent_timestamp` and `consent_text` in the `avatars` table.
3. If the creator uploads someone else's content, liability is on the creator per ToS.

### TikTok Compliance

- Luminacast does NOT automate any TikTok actions except RTMP streaming (which is a standard supported feature).
- We do NOT automate product pinning, account login, or any UI interactions.
- Chat messages are sent via TikTok-Live-Connector's chat API (unofficial but widely used).
- Creators are advised to disclose AI avatar usage per TikTok's AI content guidelines.

---

## 24. TESTING INFRASTRUCTURE & AUTOMATION

### Chat AI Safety Constraints

```python
# Chat AI system prompt (injected before every LLM call)
CHAT_SYSTEM_PROMPT = """
You are a friendly shopping assistant for a TikTok Live stream.

HARD RULES:
- Maximum response length: 280 characters
- NEVER make medical claims ("cures", "treats", "heals")
- NEVER make financial guarantees ("you'll make money", "guaranteed results")
- NEVER offer discounts not already in the product data
- NEVER share personal information about the creator
- NEVER engage with hateful, sexual, or abusive messages — ignore them
- Respond in the same language the viewer used
- Be enthusiastic but honest
- If you don't know the answer, say "Great question! The creator will answer that shortly."

CONTEXT:
Current product: {product_name} — ${product_price}
Product description: {product_description}
Last 20 chat messages: {recent_messages}
Creator persona: {persona_summary}
"""
```

### Test Automation Scope

| Test type | Scope | Tool | When |
|-----------|-------|------|------|
| **Unit tests** | Mashup engine, variant scoring, billing calc, chat classifier | pytest | Every commit (CI) |
| **Integration tests** | API endpoints with DB, R2 mock, Stripe mock | pytest + httpx | Every commit (CI) |
| **Frontend unit** | Zustand stores, utility functions | Vitest | Every commit (CI) |
| **E2E: Onboarding** | Journey 1: signup → avatar creation | Playwright | Nightly |
| **E2E: Cast Builder** | Journey 2: create Cast → pay → generation mock | Playwright | Nightly |
| **E2E: Live Control** | Journey 3: start stream mock → chat → pin confirm | Playwright | Nightly |
| **E2E: Analytics** | Journey 4: view session data → variant scores | Playwright | Nightly |
| **Load: streams** | 3 concurrent FFmpeg processes | Manual + monitoring | Pre-launch |
| **Load: chat** | 100 messages/minute throughput | k6 or locust | Pre-launch |
| **Load: RunPod** | 50 simultaneous clip jobs | Manual + RunPod dashboard | Pre-launch |
| **Manual only** | 3-hour live stream stability on real TikTok | Human QA | Pre-launch |

### Test Database

```python
# Use separate test database
# pytest conftest.py
@pytest.fixture
def test_db():
    engine = create_engine("postgresql://test:test@localhost:5433/luminacast_test")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
```

---

## 25. DOCKER COMPOSE & DEPLOYMENT

### docker-compose.yml

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    restart: always

  neo4j:
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: ${NEO4J_USER}/${NEO4J_PASSWORD}
    volumes:
      - neo4j_data:/data
    ports:
      - "7687:7687"
      - "7474:7474"
    restart: always

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: always

  orchestrator:
    build: ./backend/orchestrator
    env_file: .env
    depends_on:
      - postgres
      - neo4j
      - redis
    ports:
      - "8000:8000"
    volumes:
      - stream_cache:/tmp/stream_cache
    restart: always
    command: >
      sh -c "alembic upgrade head && 
             uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2"

  celery-worker:
    build: ./backend/orchestrator
    env_file: .env
    depends_on:
      - postgres
      - redis
    volumes:
      - stream_cache:/tmp/stream_cache
    restart: always
    command: celery -A tasks worker -l info --concurrency=4

  celery-beat:
    build: ./backend/orchestrator
    env_file: .env
    depends_on:
      - redis
    restart: always
    command: celery -A tasks beat -l info

  chat-monitor:
    build: ./backend/chat-monitor
    env_file: .env
    depends_on:
      - orchestrator
    restart: always

  frontend:
    build: ./frontend/companion-app
    ports:
      - "3000:80"
    restart: always

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./infra/nginx/luminacast.conf:/etc/nginx/conf.d/default.conf
      - certbot_certs:/etc/letsencrypt
    depends_on:
      - orchestrator
      - frontend
    restart: always

volumes:
  postgres_data:
  neo4j_data:
  stream_cache:
  certbot_certs:
```

### nginx config

```nginx
# infra/nginx/luminacast.conf
server {
    listen 80;
    server_name luminacast.com app.luminacast.com;

    # API
    location /api/ {
        proxy_pass http://orchestrator:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://orchestrator:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }

    # Frontend
    location / {
        proxy_pass http://frontend:80;
    }

    # File upload size
    client_max_body_size 100M;
}
```

### Deployment Script

```bash
#!/bin/bash
# infra/scripts/deploy.sh
cd /opt/luminacast-omni
git pull origin main
docker compose build --no-cache
docker compose up -d
docker compose exec orchestrator alembic upgrade head
echo "Deployed at $(date)"
```

---

## 26. BUILD PLAN (4 Weeks)

### Week 1: Core Pipeline

**Directory focus:** `backend/orchestrator/services/`, `runpod/`, `infra/`

- [ ] Initialize GitHub repo, set up monorepo structure
- [ ] Set up Docker Compose on Hostinger VPS (postgres, redis, neo4j, nginx)
- [ ] Configure Sentry projects (backend + frontend)
- [ ] Build service adapters: `r2_storage.py`, `fish_audio.py`, `runpod.py`, `openrouter.py`, `apify_tiktok.py`
- [ ] Deploy InfiniteTalk on RunPod Serverless (Docker container + handler)
- [ ] Test: generate 1 clip end-to-end (image + audio → video in R2)
- [ ] Build FFmpeg RTMP pipeline: `engine/streamer.py` as Celery task
- [ ] Test: stream 3 clips to test TikTok account
- [ ] Write unit tests for service adapters (mocked)

### Week 2: Orchestrator + UI + Cast Builder

**Directory focus:** `backend/orchestrator/routers/`, `frontend/companion-app/`

- [ ] Set up SQLAlchemy models + Alembic migrations
- [ ] Build auth system: register, login, JWT, role middleware
- [ ] Build Cast CRUD endpoints + Cast generation pipeline (Celery task)
- [ ] Build mashup engine: `engine/mashup.py` with weighted selection
- [ ] Set up React app: Vite + shadcn/ui + Zustand + React Query
- [ ] Build Dashboard, Cast Builder (step-by-step), Setup screens
- [ ] Build avatar clone pipeline (Apify → Fish Audio → InfiniteTalk → persona LLM)
- [ ] Wire WebSocket: connection manager + stream state events
- [ ] Build Live Control screen: NOW PLAYING, timeline, controls
- [ ] Write unit tests: mashup engine, billing calculations

### Week 3: Chat AI + Product Pinning + Analytics

**Directory focus:** `backend/chat-monitor/`, `backend/orchestrator/engine/`

- [ ] Set up Node.js chat monitor (TikTok-Live-Connector → WS relay)
- [ ] Build chat classifier: keyword match → LLM fallback
- [ ] Build chat panel in React: messages, drafts, approve/reject, multi-operator
- [ ] Implement AFK timer + auto-basket overlay logic
- [ ] Build product pin confirmation flow (WS event + companion app button)
- [ ] Set up Neo4j schema + purchase event ingestion
- [ ] Build Analytics screen in React
- [ ] Build per-variant performance scoring
- [ ] Write Playwright E2E tests for Journeys 1-2
- [ ] Implement chat AI safety constraints + rate limiting

### Week 4: Billing + Admin + Testing

**Directory focus:** `backend/orchestrator/routers/admin.py`, `frontend/`

- [ ] Integrate Stripe: Cast payments, streaming metered billing, webhooks
- [ ] Build Admin panel screen: overview, creators, streams, health, alerts
- [ ] Implement server health monitoring (psutil → alert thresholds)
- [ ] Implement scaling alerts (concurrent streams → notification)
- [ ] Write Playwright E2E tests for Journeys 3-4
- [ ] Run load tests: 3 concurrent streams, 100 msg/min chat
- [ ] End-to-end test: real TikTok Live stream, 1 hour minimum
- [ ] Bug fixes, UI polish, Sentry error review
- [ ] Write README.md with setup instructions

---

## 27. COST MODEL

*(Identical to v3 brief Section 18. Fixed: ~$15/month. Variable: $3.78/Cast, $0.005/min streaming. Creator pricing: $14.99/Cast, $0.03/min. Revenue projections at 5/20/50 creators.)*

---

*End of Development Tech Brief v4.1*
*Ready for development. Week 1 starts now.*
