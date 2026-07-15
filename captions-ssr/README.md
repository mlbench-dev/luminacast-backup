# captions-ssr — Remotion SSR caption-render service (Step 11 scaffold)

A small Node/Remotion service that renders the caption track to a
**transparent overlay** (PNG sequence or alpha video) using the **exact same**
preset table and per-token animation logic as the editor preview. The goal is
that burnt captions match the animated preview pixel-for-pixel.

> **Scaffold only.** This service is **not** wired into
> `services/cast_ffmpeg_composer.py` yet — that is Step 12. Nothing in the
> pipeline calls it until `CAPTIONS_SSR_ENABLED=true` and the composer change
> lands.

## Why this exists

The current burn path approximates the animations with stacked FFmpeg
`drawtext` filters (`cast_ffmpeg_composer.py`, the caption section). It can
only swap colours and nudge x-offsets — it cannot reproduce the bounce /
glow / typewriter / wobble animations the Remotion preview shows. Rendering
the caption track with Remotion server-side produces an overlay that *is* the
preview, which the composer can later alpha-composite over the video.

## Single source of truth

The animations and styles live in two frontend files:

- `frontend/companion-app/src/lib/captionPresets.ts` — the 15 presets and
  their `animation` keys.
- `frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/caption-page.tsx`
  — `getWordStyle()` (the per-token animation switch) and `CaptionPage`.

This service **does not copy or fork** that logic. At Docker build time the
two files are copied verbatim into `src/frontend-mirror/` (see the
`Dockerfile`), and the Remotion composition imports `CaptionPage` from there.
The only local code in `frontend-mirror/` are two tiny shims that satisfy
`caption-page.tsx`'s transitive imports without dragging in the editor's
state/UI graph:

- `items/text/text-item-type.ts` — the `FontStyle` / `TextAlign` /
  `TextDirection` types only.
- `inspector/controls/font-style-controls/font-style-controls.tsx` —
  `turnFontStyleIntoCss` only (byte-for-byte identical to the frontend).

The two copied files are `.gitignore`d here so they are never edited in two
places. For local development run `./sync-frontend.sh` to copy them in.

## Architecture

```
worker ──HTTP POST /render──▶ express (src/server.ts)
                                  │  normalize tokens, build input props
                                  ▼
                          @remotion/renderer
                                  │  selectComposition + renderFrames/renderMedia
                                  ▼
                       CaptionComposition.tsx
                                  │  createTikTokStyleCaptions  (same call as
                                  │  the editor's captions-layer.tsx)
                                  ▼
                          CaptionPage (frontend-mirror)
                                  │  getWordStyle() per token  → transparent overlay
                                  ▼
                 PNG sequence (captions-%04d.png) | alpha .webm
```

- `src/CaptionComposition.tsx` — the Remotion composition. Accepts the worker
  props, builds `@remotion/captions` `Caption[]` from the word timings, groups
  them into TikTok-style pages with `createTikTokStyleCaptions`, and renders
  each page through the shared `CaptionPage` on a transparent `AbsoluteFill`.
- `src/Root.tsx` — registers the `CaptionOverlay` composition. `width`,
  `height`, `fps`, and `durationInFrames` are resolved per request via
  `calculateMetadata` from the canvas size and the last token's end time.
- `src/server.ts` — HTTP entry. Bundles the Remotion project once at boot and
  reuses the serve URL for every render.

## HTTP API

### `POST /render`

Request body:

```jsonc
{
  "tokens": [                       // WhisperX word timings (props._captions_tokens shape)
    {"text": "Listen", "startMs": 0,   "endMs": 400},
    {"text": "up",     "startMs": 400, "endMs": 700}
  ],
  "presetId": "hormozi_bold",       // any id from captionPresets.ts (default: hormozi_bold)
  "canvas": {                       // slot resolution / frame rate
    "width": 1080,
    "height": 1920,
    "fps": 30
  },
  "block": {                        // optional per-block overrides (mirror CaptionsItem)
    "pageDurationInMilliseconds": 1200,
    "switchCaptionsEveryMs": 1200,
    "fontFamily": "",               // "" => use the preset's font
    "fontStyleVariant": "normal",
    "fontStyleWeight": "800",
    "lineHeight": 1.2,
    "letterSpacing": 0,
    "maxLines": 2,
    "captionWidth": 972             // default: canvas.width * 0.9
  },
  "format": "png-sequence"          // "png-sequence" | "alpha-video" (default by env)
}
```

`tokens` also accepts the legacy `{text, startInSeconds, endInSeconds}` shape
the composer still tolerates; it is normalised to `startMs`/`endMs`.

Response (`200`):

```jsonc
{
  "outputPath": "/tmp/captions_ssr/<job-uuid>",  // dir of PNGs, or the .webm path
  "format": "png-sequence",
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "durationInFrames": 48,
  "elapsedMs": 1234
}
```

Errors: `400` (no tokens) / `500` (render failed). The `500` message is kept
engine-agnostic; full detail is logged server-side.

### `GET /health`

Returns `{"status":"ok"}`.

## Environment variables

All flags/thresholds are env-overridable; no hardcoded timeouts.

| Var | Default | Purpose |
| --- | --- | --- |
| `CAPTIONS_SSR_ENABLED` | `false` | Composer-side gate (read in Step 12). |
| `CAPTIONS_SSR_URL` | `http://captions-ssr:3030` | Where the worker reaches this service. |
| `CAPTIONS_SSR_PORT` | `3030` | Listen port. |
| `CAPTIONS_SSR_HOST` | `0.0.0.0` | Listen host. |
| `CAPTIONS_SSR_FORMAT` | `png-sequence` | Default output format. |
| `CAPTIONS_SSR_OUTPUT_DIR` | `<tmp>/captions_ssr` | Where overlays are written. |
| `CAPTIONS_SSR_CONCURRENCY` | (Remotion default) | Render worker threads. |
| `CAPTIONS_SSR_BODY_LIMIT` | `32mb` | Max request body (long token lists). |

## Local checks

```bash
# 1. Pull the two frontend caption sources into the mirror.
./sync-frontend.sh

# 2. Install deps (NOT done in this repo's CI; the Docker image installs them).
npm install

# 3. Schema/helper smoke test — no Chromium needed.
npm run smoke

# 4. Type check.
npm run typecheck
```

## Container smoke test

```bash
# From the repo root (build context must be the repo root).
docker compose up --build captions-ssr

# In another shell — render a hormozi_bold overlay for a sample token set.
curl -sS -X POST http://127.0.0.1:3030/render \
  -H 'content-type: application/json' \
  -d '{
    "presetId": "hormozi_bold",
    "canvas": {"width": 1080, "height": 1920, "fps": 30},
    "tokens": [
      {"text": "Listen", "startMs": 0,    "endMs": 400},
      {"text": "up",     "startMs": 400,  "endMs": 700},
      {"text": "this",   "startMs": 700,  "endMs": 950},
      {"text": "works",  "startMs": 950,  "endMs": 1400}
    ]
  }'
```

> The container has no published host port in `docker-compose.yml` (it is
> internal-only on the worker network). To curl it from the host as above,
> either add a temporary `ports: ["127.0.0.1:3030:3030"]` mapping or
> `docker compose exec captions-ssr` and curl `localhost:3030` from inside.

## Verify checklist (run after merge, on the VPS)

- [ ] `docker compose up --build captions-ssr` boots and logs
      `listening on http://0.0.0.0:3030`.
- [ ] The sample `hormozi_bold` curl above returns `200` with an `outputPath`.
- [ ] The PNG sequence at `outputPath` is transparent except for the caption
      text, and the **active word lights up** in the preset's
      `highlightColor` (gold for `hormozi_bold`) on the frame matching each
      token's `[startMs, endMs]` window — matching the editor preview.
