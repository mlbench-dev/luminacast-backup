## AMENDMENT (2026-06-15) — Build & Resource Discipline (supersedes prior conflicting guidance)

> The VPS has repeatedly hit 100% CPU and been throttled by the host. Root causes found: blanket `--no-cache` rebuilds, runtime `pip install`, and a CPU-only `audio-separator` running locally instead of using the cloud path. The following rules are mandatory and **supersede any conflicting prior instruction in this file** (notably the §9 deployment-protocol "always rebuild with `--no-cache`" guidance).

### 1. Docker builds: cache-on by default
- Default deploy build is `docker compose build` (cache reuse). **Do NOT use `--no-cache`** as the default.
- `--no-cache` is permitted ONLY when a dependency or base image actually changed, and you state why in the report.
- The §9 "prove you deployed" requirement is satisfied by **verifying the container build timestamp and running code**, NOT by forcing a from-scratch rebuild. Keep the timestamp/grep verification; drop the blanket `--no-cache`.

### 2. No runtime installs
- All system/Python/Node dependencies must be declared in the Dockerfile / requirements and baked into the image at build time.
- **No `pip install`, `apt install`, or `npm install` at container runtime or mid-deploy.** If a package is missing at runtime, that is a Dockerfile bug to fix — not something to install live.
- Never use `pip install --no-cache-dir` in an interactive/runtime context. In Dockerfiles, pin versions so layers cache.

### 3. Audio separation: cloud only — no local CPU separation
- Voice/stem separation goes to **fal.ai audio separator**, with the **RunPod BS-RoFormer endpoint (`2oivc3eustr3u5`)** as fallback.
- **Do NOT install or run `audio-separator` / BS-RoFormer locally on the VPS.** The VPS has no GPU; CPU separation pegs the host.
- If the cloud separation path fails, the correct behavior is retry → Sentry → fail the affected step — **never** silently degrade to a local CPU `audio-separator` install. Find and remove any code path or fallback that triggers a local install (grep `audio-separator`, `audio_separator`).

### 4. Never overlap a build with a render
- Do not trigger a verification render while an image/frontend build is running. Sequence strictly: build → verify image timestamp → then render. Concurrent build + render is a primary cause of the CPU plateau (esbuild + ffmpeg at once).

### 5. Render worker concurrency
- The `renders` Celery queue must run at **concurrency 1–2** on a single small VPS (ffmpeg is itself multi-threaded; 4 parallel jobs saturate the box). Do not raise it without an explicit instruction and headroom.

### 6. Before any long CPU job, sanity-check headroom
- If a step will run a heavy local job, first confirm the box isn't already loaded (`ps aux --sort=-%cpu | head`). Don't stack heavy jobs.

**Report adherence** to these in every deploy PR (build mode used and why; confirmation no runtime install ran; render concurrency setting).

---

## Permanent Rule — Monitoring is mandatory

Before starting any session, after every commit, and during QA gates:

1. Read Sentry for new errors since the last check. Both projects:
   - luminacast-orchestrator (DSN: https://f5b1001e06c6ed4de241958896976350@o4511101225598976.ingest.us.sentry.io/4511146377871360)
   - luminacast-gpu-worker (DSN: https://cece2f997e59df01ef096c6c2366787e@o4511101225598976.ingest.us.sentry.io/4511146388619264)

2. Read BetterStack tail for the last 5 minutes of logs across all three sources:
   - luminacast-vps (token: km6Lo7gBoX8NQrWU9Embi4Gv)
   - luminacast-gpu (token: m2z2NcGy3L8qXNW59Nm679tm)
   - luminacast-docker (token: E2DKVqGkdgQQcK3imQt7veCq)

3. Use the helper script `./scripts/check_monitoring.sh` (created in Session CD).

4. If new errors are present, **investigate and fix BEFORE proceeding** with the planned work. Errors found in monitoring are real production failures and take priority over planned features.

5. Document what was found and what was fixed in STATUS.md under the current session header. If no errors found, say so explicitly: `Monitoring check: clean`.

Failure to follow this rule means working on planned features while production is broken — wasted hours and unhappy users.

---

# RULES.md — Permanent Operating Protocol

**Read this file FIRST at the start of every session. Before STATUS.md, before the Dev Brief, before anything else. These rules apply to every task, every session, regardless of context.**

---

## 1. Session Start Ritual (Do This Every Time)

```
1. Read this file (RULES.md)
2. Read STATUS.md
3. Read any Dev Brief or instruction provided
4. Check recent git log: `git log --oneline -20`
5. Check server health: `docker compose ps` and `docker compose logs --tail=20`
6. Check Celery worker status: `docker compose logs celery-worker --tail=20`
7. ONLY THEN begin working
```

If any step reveals a problem (crashed container, failed migration, error in logs), report it before proceeding.

---

## 2. Before Every Delivery: Mandatory QA

Before telling me "done" or "fixed" or "deployed," run this checklist:

**Frontend changes:**
- [ ] `npm run build` passes with zero errors
- [ ] No TypeScript errors (`npx tsc --noEmit`)
- [ ] Open the page in a browser (or describe what the user will see step by step)
- [ ] Click through the flow you changed — does it work end to end?

**Backend changes:**
- [ ] Server starts without import errors (`docker compose up orchestrator` — check logs for tracebacks)
- [ ] Celery worker starts without errors (`docker compose logs celery-worker --tail=10`)
- [ ] Hit the endpoint you changed with a test request (curl or httpie) — show me the response
- [ ] If you added a new endpoint, show me the curl command and the actual response

**Database changes:**
- [ ] `alembic upgrade head` succeeds
- [ ] `alembic downgrade -1` succeeds (migration is reversible)
- [ ] `alembic upgrade head` again (round-trip works)

**All changes:**
- [ ] `git diff --stat` — show what files changed
- [ ] No unintended file modifications
- [ ] STATUS.md updated

**If any check fails, fix it before declaring done.** Do not tell me something is fixed if you haven't verified it works.

**User Journey QA (most important):**

Unless I specifically ask for a broader scope, your QA must walk the **actual user journey** — from the beginning of the flow through the point where the bug occurred, and one step beyond. Do not test the entire application end-to-end when only the clone flow is being built. The product is in active development — many later sections don't work yet and that's expected.

Example: If you fixed a bug in the segment selector (Section C), your QA should cover:
1. Enter a TikTok handle (Section A) — does the gallery load?
2. Click a video (Section B) — does it download and open in the segment selector?
3. Adjust handles and click "Use This Segment" (Section C) — does it submit successfully?
4. Does the "faces" step appear with a loading spinner? (Section D — one step beyond the fix)

Do NOT test Cast Builder, Streaming, Analytics, or anything unrelated to the flow you touched. Focus on what the user actually experiences in sequence.

**Playwright smoke test (frontend changes to clone flow):**

For any frontend change that affects the clone flow, run a headless Playwright test that:
1. Logs in
2. Navigates to Setup
3. Enters a TikTok handle and fetches videos
4. Selects a video, opens the segment selector
5. Clicks "Use This Segment"
6. Confirms: zero console errors, DOM is not empty, the faces/progress step renders

If the smoke test fails, the fix is not done.

---

## 3. When I Report a Bug or Problem

When I say something like "it's broken", "blank screen", "nothing happens", "it's stuck", or describe any unexpected behavior:

**Step 1: Gather evidence BEFORE guessing**

```bash
# Check what I was doing — recent API calls in server logs
docker compose logs orchestrator --tail=100 | grep -i "error\|fail\|exception\|trace"

# Check Celery for task failures
docker compose logs celery-worker --tail=100 | grep -i "error\|fail\|exception\|trace"

# Check if any containers crashed
docker compose ps

# Check the database for the latest avatar/cast state
# (use psql or a quick Python script)

# Check recent git history — what changed last?
git log --oneline -10
git diff HEAD~1 --stat
```

**Step 2: Identify the EXACT failure point**
- Which endpoint returned an error? (check orchestrator logs)
- Which Celery task crashed? (check celery-worker logs)
- What was the error message and full traceback?
- What was the state of the relevant database record?

**Step 3: Report what you found**

Tell me:
- "The error is: [exact error message]"
- "It happens in: [file:line]"
- "The cause is: [explanation]"
- "The fix is: [what you'll change]"

**Step 4: Research before you fix**

Before writing any code, take 60 seconds to think:

- **Is this a known issue with the library/framework involved?** Search your knowledge for common pitfalls with FastAPI async, SQLAlchemy session management, React Query caching, Celery task serialization, R2/S3 CORS, ffmpeg edge cases, etc.
- **Have other developers hit this exact pattern?** Think about whether this is a well-documented gotcha — for example: "React state update on unmounted component," "Celery task losing database session after fork," "boto3 upload missing Content-Type," "ffmpeg faststart not applied."
- **What's the standard fix in the ecosystem?** If 1,000 developers have solved this before, use their proven solution — don't invent a novel approach.
- **Could your fix introduce a regression?** Think about what else touches the same code path. If you change how `face_ref_key` is set, check every place that reads `face_ref_key`.

Write a brief note (2-3 sentences) in your response explaining what you researched and why you chose your approach. This prevents "fix one bug, create two more."

**Step 5: ONLY THEN fix it**

Do not guess. Do not make changes based on theory. Read the logs first.

---

## 4. Log Everything in STATUS.md

**When you find a bug:**
Add to BUGS FOUND with next sequential number, severity, OPEN status, today's date.

**When you fix a bug:**
Change status to FIXED. Add file(s) changed. Add row to CHANGES APPLIED.

**When you start something but can't finish:**
Add to CHANGES PENDING with what's blocked and the next step.

**When you need my input:**
Add to PENDING DECISIONS. Stop working on that item. Ask me.

**When you make ANY file change:**
Log it in CHANGES APPLIED with timestamp, file path, what changed, and why.

**Architecture Decisions (AD-xxx):**
These are LOCKED. Do not revisit, question, or refactor them. If you think one is wrong, add a PENDING DECISION asking me — do not change it yourself.

---

## 5. Code Principles

- **Prefer fixing over refactoring.** If something works but is ugly, leave it. If something is broken, fix only the broken part.
- **Never touch files you weren't asked to touch.** If you notice a problem in an unrelated file, log it as a bug in STATUS.md — don't fix it unless asked.
- **Every external API call must be in a service file** (`services/*.py`) so it can be mocked in tests.
- **Never store full URLs in the database.** Store R2 keys. Append the CDN domain at the API layer.
- **Never use `Base.metadata.create_all` for schema changes.** All changes go through Alembic.
- **Every foreign key gets `index=True`.**
- **Log with structured JSON.** Fields: service, level, message, timestamp, plus relevant IDs.
- **Error handling on every external call.** Retry with exponential backoff, max 3, then fail gracefully.

---

## 6. Server Access Reference

```
VPS: root@145.223.121.28 (Hostinger KVM 4, Ubuntu)
Project: /opt/luminacast-omni
Logs: docker compose logs [service] --tail=N
Services: orchestrator, celery-worker, celery-beat, postgres, neo4j, redis, nginx, frontend, chat-monitor
Database: docker compose exec postgres psql -U luminacast -d luminacast
Redis: docker compose exec redis redis-cli
```

---

## 7. When You Restart / Context is Lost

If you don't remember what you were doing, or this is a fresh session:

1. Read this file (you're doing it now)
2. Read STATUS.md — it tells you what's OPEN, what's PENDING, what's been FIXED
3. Run `git log --oneline -10` to see recent changes
4. Run `docker compose ps` and `docker compose logs --tail=20` to check server health
5. Ask me "What would you like me to work on?" if STATUS.md doesn't make it clear

**Never assume you know the current state. Always check.**

---

## 8. MANDATORY END-TO-END FRONTEND QA — NEVER SKIP THIS

**THIS IS THE MOST IMPORTANT RULE. EVERY SESSION. EVERY DEPLOY. NO EXCEPTIONS.**

After ANY code change that touches the frontend, backend API, or generation pipeline, you MUST run a full Playwright browser QA covering both test flows below. If you cannot complete them, the session is NOT done. Do not push. Do not declare victory. Fix it first.

### TEST FLOW A — Clone Avatar (run after every avatar-related change)

Using Playwright (headless Chrome), logged in as admin:

1. **Avatar Library check**: Navigate to `/my-avatar`. Confirm the page loads, avatars render with thumbnails and status badges. No 404, no blank screen.
2. **Create clone**: Click "Social media" → enter a TikTok URL of a real person (use `@barstoolsports` or any known account). Wait for video gallery to load.
3. **Pick segment**: Select the first video, pick a 20-30s segment, click "Use This Segment".
4. **Face selection**: Wait for face candidates. Confirm 4+ face thumbnails appear. Select the best one.
5. **Confirm + generate**: Click through to "Preview Script & Generate". Accept the default test script.
6. **Watch generation**: Monitor until avatar reaches READY status. Confirm test video appears.
7. **Approve**: Click Approve. Confirm avatar card shows "Approved" badge.
8. **VERIFY IN BROWSER**: Navigate to avatar library. Find the new avatar. Click it. Confirm the test video plays.

**PASS condition**: Avatar card visible in library, test video plays, status = Approved.

### TEST FLOW B — Cast with Product (run after every cast-related change)

Using Playwright (headless Chrome), logged in as admin:

1. **Cast Builder**: Navigate to `/cast-builder/new`. Confirm page loads.
2. **Select avatar**: Pick an APPROVED avatar (not just any avatar — must have status=approved).
3. **Select product**: Pick any product from the library. Confirm it has an image (naturalWidth > 0).
4. **Quality**: Select Simple ($14.99).
5. **Continue to Scripts**: Click "Continue to Scripts →". Confirm step 2 loads with block editor.
6. **Check blocks**: Confirm drag handles (⋮⋮), active checkboxes, delete buttons, and dual text fields (Spoken Text + Motion & Gestures) are all visible. Confirm character counter shows "X/280 chars".
7. **Generate Audio Preview**: Click "Pay $14.99 & Generate Audio Preview". Confirm API calls succeed (no 400/500). Wait for "Audio Preview Ready" screen.
8. **Generate Videos**: Click "Generate Videos". Confirm InfiniteTalk jobs are submitted.
9. **Wait for READY**: Poll cast status until it reaches READY (at least 1/N clips ready).
10. **VERIFY IN BROWSER**: Navigate to `/cast-builder`. Find the cast. Click it. Confirm variant video plays with product overlay visible.

**PASS condition**: Cast in READY status, at least 1 clip plays, product overlay visible on video.

### TEST FLOW C — AI Avatar (run after every AI avatar change)

1. Navigate to `/my-avatar`. Click "Create character →".
2. Enter description: "energetic male fitness influencer with short dark hair".
3. Pick voice: select any voice option.
4. Preview & Approve: confirm test video renders.
5. Approve avatar. Confirm it appears in Cast Builder avatar picker.

**PASS condition**: AI avatar approved and selectable in Cast Builder.

### QA Reporting Format

After every QA run, post a table in STATUS.md:

```
| Flow | Step | Result | Screenshot/Evidence |
|------|------|--------|---------------------|
| Clone Avatar | Face candidates | PASS | 8 thumbnails visible |
| Clone Avatar | Test video plays | PASS | video src loaded |
| Cast | Audio Preview | PASS | TTS completed in 42s |
| Cast | Generate Videos | PASS | 10 jobs submitted |
| Cast | Clip plays | FAIL | video src empty — webhook bug |
```

**If ANY step FAILS, fix it before closing the session. The session is not done until both flows pass end-to-end.**

---

## Invariants added 2026-04-07 (B-127, B-128, B-129)

- Never use getattr() as a silent fallback for model attributes. If a field
  doesnt exist on the model, call the response-builder helper that computes it,

## Invariants added 2026-04-07 (B-127, B-128, B-129)

- Never use getattr() as a silent fallback for model attributes. If a field
  does not exist on the model, call the response-builder helper that computes it,
  or query the source-of-truth table directly. The cover_image_url vs
  cover_image_key confusion in B-127 was a textbook example of this failure mode.

- Every exception handler around a compositing or rendering step MUST record the
  failure on the variant row, not just log it. Users and the frontend must be
  able to see degraded generations. Silent degradation is worse than failure.

- Compositing code paths (base64 webhook, URL webhook, legacy, scene_objects)
  MUST share a single implementation. Never duplicate compositing logic across
  multiple webhook handlers or code paths.

## Invariants added 2026-04-07 (B-130/131/132/133)

- Never POST an empty JSON body to a paid Apify actor. Always set maxItems or
  equivalent to a non-default value. The default is designed for trial/sampling
  and causes silent pagination failures downstream.

- Cross-actor ID matching is unreliable. Always match by stable identifiers like
  URLs or normalised titles. productId schemes differ across Apify actors.

- Any column declared in a model MUST be populated by at least one code path,
  or removed from the model. Dead columns cause silent UX degradation.

- Cache shortcuts must respect pagination: returning zero items on page 2+ with
  a non-zero page 1 total is a cache-miss, not a no-more-results signal.

## Invariants added 2026-04-07 (B-134 pro100chok rewire)

- ALWAYS read the real API response status codes before writing error handlers.
  run-sync-get-dataset-items returns 201 (Created), not 200. Checking != 200 silently
  drops every enrichment result. Verify with a live test run before writing the check.

- ALWAYS verify actor output schema with a real call before writing field mappings.
  pro100chok returns specifications as a dict {key: value}, not a list. Document
  mismatches in the enrichment function docstring so the next engineer knows.

- STEP 1 diagnostic output MUST be pasted into STATUS.md before any code is written.
  This is not optional. It is the contract between the schema and the mapping code.

## Bug Investigation — Mandatory First Steps

Before investigating ANY bug, error, or unexpected behavior:

1. Run `./scripts/sentry_check.sh` — list recent Sentry issues for both projects
2. Run `./scripts/betterstack_check.sh` — query last hour of logs from all 3 sources
3. Cross-reference any findings with the bug timeline
4. Only after the above, fall back to `docker compose logs` and DB queries

Handled errors must be captured. When a Celery task or GPU endpoint catches an exception and returns 500, it MUST also call `sentry_sdk.capture_exception(e, extras={...})` so Sentry sees it.
## 9. MANDATORY DEPLOYMENT PROTOCOL — NEVER SKIP

**Every code change MUST be deployed to the live VPS and verified running before declaring "done." Pushing to GitHub is NOT deploying.**

```bash
# STEP 1 — Push to GitHub
git push origin main

# STEP 2 — Pull on VPS (SSH or exec from this shell)
cd /opt/luminacast-omni
git pull origin main

# STEP 3 — Verify HEAD matches
LOCAL_HEAD=$(git rev-parse HEAD)
echo "VPS HEAD: $LOCAL_HEAD"
# Must match the commit you just pushed. If not, stop.

# STEP 4 — Rebuild ALL affected containers (cache-on by default; see AMENDMENT §1)
# Frontend change:
docker compose build frontend && docker compose up -d frontend

# Backend change:
docker compose build orchestrator celery-worker && docker compose up -d orchestrator celery-worker

# Both:
docker compose build frontend orchestrator celery-worker && docker compose up -d frontend orchestrator celery-worker

# Add --no-cache ONLY when a base image or dependency actually changed (e.g. a
# new line in requirements.txt or a changed Dockerfile FROM). State the reason
# in your deploy report. Otherwise cache reuse is correct and far cheaper —
# blanket --no-cache rebuilds are what pegged the VPS at 100% CPU.

# STEP 5 — Verify the NEW container is running (not the old one)
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.CreatedAt}}"
# CreatedAt MUST be within the last 2 minutes. If it shows an old timestamp, the rebuild failed silently.

# STEP 6 — Verify the code is actually inside the running container
docker compose exec frontend cat /app/package.json | grep '"version"'
# Or for backend:
docker compose exec orchestrator git log --oneline -1 2>/dev/null || docker compose exec orchestrator python -c "import config; print('OK')"

# STEP 7 — Smoke test the change
# Hit the endpoint or page you changed. Confirm the fix is visible.
```

**Rules:**
- `git push` alone is NOT a deployment. The user will see no change.
- `docker compose up -d` without a preceding `docker compose build` reuses the old image. Always rebuild the affected services (cache-on `docker compose build <services>`; add `--no-cache` only when a dependency/base image changed — see AMENDMENT §1), then verify the fresh container timestamp in Step 5.
- If CreatedAt on the container is older than your last push, you did NOT deploy.
- NEVER say "deployed" or "live" or "hard refresh should work" until Step 5 confirms a fresh container timestamp.
- If a subagent pushed code, YOU must still pull + rebuild on the VPS. Subagents cannot deploy.

**Common failure mode:** subagent pushes to GitHub → reports "fix deployed" → VPS still running old container → user sees no change → wastes 20 minutes debugging a "new bug" that is actually stale code.

**If you cannot SSH to the VPS or run docker commands:** say "Code pushed to GitHub but I cannot deploy from this environment. Please run: `cd /opt/luminacast-omni && git pull && docker compose build frontend orchestrator celery-worker && docker compose up -d`" (add `--no-cache` only if a dependency/base image changed — see AMENDMENT §1) — do NOT claim it's deployed.
