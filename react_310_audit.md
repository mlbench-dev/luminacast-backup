# React #310 + Body Description Debug Audit — 2026-04-14

## Phase A — Evidence Gathering (NO CODE CHANGES)

### A1: Full Error Reproduction (Sentry Source-Mapped)

**Method used:** Sentry event analysis (source-mapped production builds)

**Sentry Issues Found:**
- Issue 7409067957: "Error: Rendered more hooks than during the previous render." — 1 event, last seen 2026-04-13T20:43:30Z
- Issue 7409043152: Same error — 2 events, last seen 2026-04-13T20:42:14Z

**Exact error message:** `Rendered more hooks than during the previous render.`

**Component stack (from minified build):**
```
/assets/index-CnplDAI--v2.js:1794:121408 in mre
  Context: {className:"h-5 w-5 animate-spin text-accent"})});const[g,T]=S.useState("upload"),[y,p]=S.useState(!1),[m,A]=S.useState(null)
```

**Key identification:**
- The minified function name is `mre`
- The context shows `S.useState("upload")` — this matches `VoiceCorpusTab.tsx:93` where `useState<"record" | "upload">("upload")` is declared
- The `className:"h-5 w-5 animate-spin text-accent"` matches the loading spinner at `VoiceCorpusTab.tsx:88`

**Breadcrumbs from Sentry event 7409043152:**
```
[ui.click] img.w-full.h-full.object-cover (alt="Face 8")
[ui.click] button (Continue-type button)
[ui.click] button (Another action button)
[xhr] (multiple XHR requests)
[console] ErrorBoundary caught: Error: Minified React error #310
```

**Reproduction sequence:** User clicked Face 8 image → clicked Continue → progressed to Voice phase → VoiceCorpusTab loaded with isLoading=true (early return) → data loaded, isLoading=false → hooks after early return reached → #310 thrown.

---

### A2: Static Analysis — AIAvatarSetup.tsx

```
ShimmerField: 0 hooks, 1 top-level returns, 1 early if-returns
FacePreviewModal: 1 hooks, 1 top-level returns, 0 early if-returns
SetupPhase: 16 hooks, 1 top-level returns, 0 early if-returns
FacePhase: 4 hooks, 1 top-level returns, 0 early if-returns
VoicePhase: 10 hooks, 1 top-level returns, 0 early if-returns
BodyShotsPhase: 2 hooks, 1 top-level returns, 0 early if-returns
PreviewPhase: 6 hooks, 1 top-level returns, 0 early if-returns
AIAvatarSetupPage: 17 hooks, 1 top-level returns, 0 early if-returns
```

**Conclusion:** AIAvatarSetup.tsx is clean. All components have stable hook counts with single returns and zero early if-returns. The React #310 is NOT in this file.

---

### A3: Child Component Analysis

**VoiceCorpusTab.tsx — ROOT CAUSE IDENTIFIED**

Static analysis output:
```
VoiceCorpusTab: 8 hooks, 1 top-level returns, 2 early if-returns
  *** VIOLATION: hooks AFTER early return
```

**The violation (lines 85-98):**
```tsx
// Line 31-44: First 5 hooks (useQueryClient, useRef, useQuery, useMutation x2) — OK
// Line 69-83: 2 useCallback hooks — OK

// Line 85-91: EARLY RETURN when isLoading === true
if (isLoading) {
    return (<div><Loader2 className="h-5 w-5 animate-spin text-accent" /></div>);
}

// Lines 93-98: HOOKS AFTER EARLY RETURN — VIOLATION!
const [corpusTab, setCorpusTab] = useState<"record" | "upload">("upload");   // line 93
const [isRecording, setIsRecording] = useState(false);                       // line 94
const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);        // line 95
const [recordingDuration, setRecordingDuration] = useState(0);              // line 96
const mediaRecorderRef = useRef<MediaRecorder | null>(null);                // line 97
const recordingTimerRef = useRef<NodeJS.Timeout | null>(null);              // line 98
```

**Why this causes #310:**
1. First render: `isLoading` is `true` → early return at line 85 → React sees 7 hooks (5 + 2 useCallback)
2. Data loads, re-render: `isLoading` is `false` → passes the early return → React sees 7 + 4 useState + 2 useRef = 13 hooks
3. React compares: 13 > 7 → "Rendered more hooks than during the previous render" → Error #310

**Other child components checked:**
- VoiceBrowser.tsx: 6 hooks, 1 return, 0 early if-returns — CLEAN
- AvatarIdentityPanel: referenced but not imported from a suspect path — not a concern

---

### A4: PreviewPhase useEffect + useQuery Interaction

All hooks in PreviewPhase are unconditional:
- `useNavigate()` — unconditional
- `useState(null)` — unconditional
- `useState(false)` — unconditional
- `useQuery()` — unconditional (enabled flag only controls fetching, not the hook call)
- `useEffect()` x2 — unconditional
- `useMutation()` — unconditional

**Conclusion:** PreviewPhase is NOT the source of #310.

---

### A5: Body Description Backend Error

**Sentry Issue 7407701993:**
- Title: `generate-body-description failed: Client error '400 Bad Request' for url 'https://openrouter.ai/api/v1/chat/completions'`
- Last seen: 2026-04-13T13:52:53Z (7 events total)
- Breadcrumbs show 3 retries, all returning 400

**Root cause analysis:**
- The `generate-body-description` endpoint (`avatar.py:2341`) calls OpenRouter with model `anthropic/claude-sonnet-4`
- Model ID is valid on OpenRouter (verified via live API call)
- The prompt (`gemini_body_description`) references "Analyze the face image" but the endpoint sends a **text-only** request — no image is included
- **However**, the endpoint is now DEAD CODE. The frontend comment says "body_description merged into setup" (line 25 of AIAvatarSetup.tsx)
- Body description is now generated via `rewrite-avatar-identity` endpoint, which returns `{name, description, body_description}` in a single LLM call
- `aiGenerateBodyDescription` exists in `api.ts:423` but is **never called** from any component
- The Sentry errors are from 2026-04-13, before the refactor was fully deployed
- Recent orchestrator logs (2026-04-14) show NO calls to `/api/avatar/ai/generate-body-description`
- Recent flow (avt_28fccec66ac1) went: save-setup → rewrite-avatar-identity → generate-faces → select-face → voice-corpus → generate-voice-description — all 200 OK

**The 400 error cause:** The `gemini_body_description` prompt says "Analyze the face image" but the `generate_text` method sends only text messages (no image). OpenRouter/Claude may be rejecting the request because the system prompt references image analysis but no image is provided. Alternatively, there could be a format issue in the prompt content.

**Status:** This issue is now MOOT — the endpoint is no longer called. No fix needed.

---

### A6: Cross-Reference — Are the Two Symptoms Linked?

**React #310 and body description failure are INDEPENDENT issues.**

Evidence:
1. The React #310 is caused by a hooks-after-early-return violation in `VoiceCorpusTab.tsx` — this is a pure frontend structural bug
2. The body description 400 error was a backend OpenRouter issue in a now-dead endpoint
3. The React #310 triggers when transitioning to the Voice phase (where VoiceCorpusTab is rendered), not during body description generation
4. The Sentry breadcrumbs for #310 show face selection → continue clicks, not body description calls
5. The body description endpoint is no longer called by the frontend

**Conclusion:** Fix #310 by moving hooks before the early return in VoiceCorpusTab.tsx. The body description error requires no fix — the endpoint is dead code.

---

## Summary

| Finding | Component | Root Cause | Status |
|---------|-----------|-----------|--------|
| React #310 | VoiceCorpusTab.tsx:85-98 | useState/useRef hooks declared AFTER early `if (isLoading) return` | **FIX NEEDED** |
| Body desc 400 | avatar.py:2341 generate-body-description | Dead endpoint, no longer called | **MOOT** |
| AIAvatarSetup.tsx | All 8 components | Clean — stable hooks, single returns | **VERIFIED CLEAN** |
| VoiceBrowser.tsx | VoiceBrowser | Clean — 6 hooks, no early returns | **VERIFIED CLEAN** |
| PreviewPhase | PreviewPhase | Clean — all hooks unconditional | **VERIFIED CLEAN** |
