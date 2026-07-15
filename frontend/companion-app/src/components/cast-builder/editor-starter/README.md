# Editor Starter Module

Based on **Remotion Editor Starter** — a paid template from the Remotion team.

## Source

- **Version**: Remotion 4.0.433 (all @remotion/* packages pinned to this version)
- **Purchased from**: https://www.remotion.pro/editor
- **Forked to**: `3gorka72/remotion-editor-starter` on GitHub
- **VPS reference copy**: `/opt/luminacast-omni/external/remotion-editor-starter/`
- **Copied into Luminacast**: 2026-04-15

## Integration Strategy

Strategy A — Fork into Luminacast repo (per migration plan F.1.5).

Files are copied from the Editor Starter source into this directory and adapted
for Luminacast conventions (design tokens, media panel injection, metadata
persistence, feature flag overrides). Internal directory structure is preserved
so future updates can be merged via diff.

## Local Modifications (Phase F.4)

- **flags.ts**: Disabled project management, rendering, asset upload, save/load,
  marketing features. Kept core editing features (timeline, inspector, canvas,
  playback, captioning, keyboard shortcuts).
- **editor.tsx**: Removed `h-screen w-screen` so editor fits within CastBuilder layout.
  Removed `DownloadRemoteAssets` and `UseLocalCachedAssets` (cache disabled).
- **editor-starter.css**: Replaced default colors with Luminacast design tokens
  (`bg`, `surface`, `border`, `accent`) via CSS variable overrides.
- **top-panel.tsx**: Added `LuminacastMediaPanel` as left sidebar alongside Canvas
  and Inspector.
- **items/shared.ts**: Added optional `metadata?: ItemMetadata` field to `BaseItem`
  for `block_id` persistence through all edit operations (drag, trim, split, etc.).
- **rendering/**: Stubbed — only type exports preserved. Luminacast uses its own
  render pipeline (FinalizingPhase + Celery tasks).
- **utils/server-env.ts**: Stubbed — Luminacast does not use Remotion Lambda.
- **LuminacastMediaPanel.tsx**: NEW — Adapted media panel using Editor Starter
  state actions instead of Twick API.
- **LuminacastEditor.tsx**: NEW — Wrapper component for mounting in CastBuilder.
- Removed `use client` directives (Vite SPA, not SSR).

## License

One-time purchase ($600). Source code is fully owned after purchase.
Solo-founder qualifies for free Remotion license. Revisit when crossing
Remotion threshold (4+ employees OR revenue threshold).
