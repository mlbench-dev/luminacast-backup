# React #310 + Body Description Debug — Result Summary

**Date:** 2026-04-14
**Session type:** Diagnostic + targeted fix

---

## React #310 — "Rendered more hooks than during the previous render"

### Root Cause
**File:** `frontend/companion-app/src/components/avatar/VoiceCorpusTab.tsx`
**Bug:** Lines 93-99 declared 4 `useState` + 3 `useRef` hooks AFTER an early `if (isLoading) return` at line 85.

When `isLoading` was true (first render, useQuery fetching), the component returned early and React saw 7 hooks. When data loaded and `isLoading` became false, React saw 13 hooks — triggering error #310.

### Why Previous Fix Attempts Failed
All 4 previous attempts targeted `AIAvatarSetup.tsx` — adding keys, changing phase rendering patterns, removing early returns. **The bug was never in AIAvatarSetup.tsx.** It was in the child component `VoiceCorpusTab`, which is rendered inside `VoicePhase`. The minified Sentry traces didn't identify the component clearly, leading to misdirected fixes.

### Fix Applied
Moved the 7 hook declarations (4 useState + 3 useRef) from after the `if (isLoading) return` to before it. One file, one change, 14 lines moved.

### Verification
- Static analysis confirms: 0 hooks after early returns (was 2 violations)
- TypeScript: no new errors in VoiceCorpusTab.tsx
- Vite build: succeeds
- Deployed to VPS: frontend rebuilt and serving new bundle (hash: index-CzIph5iM-v2.js)

---

## Body Description Backend Error

### Root Cause
**Sentry issue 7407701993:** OpenRouter returned 400 Bad Request to `/api/avatar/ai/generate-body-description`

### Status: MOOT — No Fix Needed
The `generate-body-description` endpoint is dead code:
- Body description generation was merged into the `rewrite-avatar-identity` endpoint (single LLM call returns name + description + body_description)
- The frontend `aiGenerateBodyDescription` API method exists in `api.ts:423` but is never called
- The Sentry errors (7 events) are from 2026-04-13, before the refactor was deployed
- Recent orchestrator logs (2026-04-14) show zero calls to this endpoint

### Cross-Reference
The two symptoms (React #310 and body description failure) are **independent**:
- React #310 is a pure frontend hook ordering bug in VoiceCorpusTab
- Body description failure was a backend OpenRouter issue in a dead endpoint
- They were never causally linked

---

## Files Changed
- `frontend/companion-app/src/components/avatar/VoiceCorpusTab.tsx` — moved hooks before early return

## Files Created (docs only)
- `react_310_audit.md` — full Phase A evidence audit
- `react_310_debug_result.md` — this summary

## Follow-Up Items
- Upload frontend source maps to Sentry on each deploy (makes future React error debugging trivial)
- Consider removing the dead `generate-body-description` endpoint and unused `aiGenerateBodyDescription` API method
