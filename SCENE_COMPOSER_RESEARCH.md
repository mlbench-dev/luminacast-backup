# Scene Composer Research Summary

## 1. Canvas Library Decision: **react-moveable**

**Chosen:** `react-moveable` (by Daybrush)
**Alternatives evaluated:** `konva.js` / `react-konva`, `fabric.js`, `react-rnd`

| Library | Drag | Resize | Rotate | Bundle Size | React Native | Verdict |
|---------|------|--------|--------|-------------|-------------|---------|
| react-moveable | Yes | Yes (8 handles) | Yes (free rotation) | ~45KB gzip | No | **Best fit** |
| react-rnd | Yes | Yes (8 handles) | No | ~15KB | No | Already used, no rotation |
| react-konva | Yes | Custom | Custom | ~120KB | No | Canvas-based, overkill |
| fabric.js | Yes | Yes | Yes | ~200KB | No | Too heavy, non-React |

**Why react-moveable:**
- Natively supports drag + resize + rotate on DOM elements (not canvas-based)
- Works with standard React refs — objects are real DOM nodes styled with CSS
- Snapping, guidelines, and grouping built in
- Used by Canva's web editor internals and Figma community plugins
- Complements our existing react-rnd (LayoutEditor) without replacing the rendering model
- Small bundle: ~45KB gzipped

**Inspired by:** Canva's free-form canvas with transform handles. Canva uses a DOM-overlay approach on top of a canvas layer — we replicate this with absolute-positioned divs + Moveable transforms.

## 2. Timeline Anchor UI Pattern

**Reference products studied:**
1. **CapCut (desktop)** — horizontal multi-track timeline below canvas, each object is a colored bar, drag left/right edge to adjust start/duration, drag up/down between tracks
2. **InShot (mobile)** — single-track timeline at bottom, tap object to see its time bar, pinch edges to adjust
3. **Mojo (mobile)** — per-slide timing with inline duration spinner, no drag timeline

**Chosen pattern:** Horizontal timeline strip below canvas (CapCut-style, simplified)
- Block boundaries shown as vertical dividers with labels
- Objects appear as colored bars (green=product, blue=text, orange=sticker, purple=sfx)
- Drag bar left edge → changes `start_ms`
- Drag bar right edge → changes `duration_ms`
- Inspector sidebar has numeric inputs for fine adjustment
- Play button scrubs through time, showing objects appear/disappear

**Why this over inline controls:** Our blocks have measurable durations (~10 chars/sec), making a visual timeline meaningful. CapCut's pattern is familiar to our TikTok-native users.

## 3. Text Effect Catalog (15 Styles)

| # | ID | Name | Font | Color | Animation | FFmpeg Render |
|---|----|------|------|-------|-----------|--------------|
| 1 | `bold_pop` | Bold Pop | Impact | White + black stroke | Scale-up bounce | `drawtext` with `borderw=4:bordercolor=black` |
| 2 | `neon_glow` | Neon Glow | Orbitron | Cyan | Pulse glow | `drawtext` + `boxblur` shadow layer |
| 3 | `glitch` | Glitch | Space Mono | White + RGB split | Flicker + offset | Triple `drawtext` with R/G/B offsets + random enable |
| 4 | `chromatic_aberration` | Chromatic | Inter Bold | White | Static RGB split | Triple `drawtext` red at -2px, cyan at +2px |
| 5 | `typewriter` | Typewriter | Courier New | Green on black | Letter reveal | Sequenced `drawtext` with expanding text via `textlen` |
| 6 | `karaoke` | Karaoke | Poppins Bold | Yellow highlight sweep | Word-by-word | Multiple `drawtext` segments, sequenced `enable` |
| 7 | `kinetic_stack` | Kinetic Stack | Montserrat Black | White | Staggered slide-in | Multiple `drawtext` with delayed `y` animation |
| 8 | `tagline_badge` | Tagline Badge | Inter Semibold | White on accent pill | Slide-in | `drawtext` with `box=1:boxcolor=#8B82C0@0.9:boxborderw=12` |
| 9 | `newspaper` | Newspaper | Playfair Display | Black | Stamp-in | `drawtext` with serif, double underline via `drawbox` |
| 10 | `graffiti` | Graffiti | Permanent Marker | Lime | Spray jitter | `drawtext` with `rotation` + shadow, pre-rendered PNG for complex |
| 11 | `shimmer` | Shimmer | Inter Bold | Gradient (gold) | Moving highlight | Pre-rendered PNG sequence (gradient mask animation) |
| 12 | `liquid` | Liquid | Fredoka | Purple | Wavy distortion | Pre-rendered PNG sequence (SVG `feTurbulence` filter baked) |
| 13 | `price_tag` | Price Tag | Roboto Mono | White on red | Tilt-in | `drawtext` with `boxcolor=red`, `rotation` expression |
| 14 | `countdown` | Countdown | Anton | White | Tick scale | Sequenced `drawtext` with decreasing number, scale pulse |
| 15 | `sparkle` | Sparkle | Poppins | White + emoji particles | Particle burst | Base `drawtext` + pre-rendered sparkle particle overlay PNG |

### CSS Preview vs FFmpeg Render Strategy

**Drawtext-capable styles** (rendered directly in FFmpeg):
`bold_pop`, `neon_glow`, `chromatic_aberration`, `tagline_badge`, `newspaper`, `price_tag`, `countdown`, `typewriter`, `karaoke`, `kinetic_stack`

**Pre-rendered PNG sequence styles** (rendered via Pillow → overlay in FFmpeg):
`glitch`, `shimmer`, `liquid`, `sparkle`, `graffiti`

For pre-rendered styles, the backend uses Pillow to render text frames as RGBA PNGs, then overlays them using FFmpeg's `movie` filter or `-i` with `overlay`.

## 4. TikTok UI Chrome Safe Zones

Based on TikTok LIVE interface analysis (2024-2025 layout):

```
┌────────────────────────────────┐
│ [LIVE] [viewers] [❤️]    [✕]  │  ← Top bar: 0-60px (LIVE badge, viewer count)
│                                │
│                                │
│                      [♡]      │  ← Right rail starts ~60% down
│                      [💬]      │     Action buttons: like, comment, share
│                      [↗️]      │     X: 85-100% width
│                      [🛒]      │     Y: 45-75% height
│                                │
│                                │
│ ┌─────────────────────────┐   │  ← Chat column: 0-65% width, 55-85% height
│ │ user1: great product!   │   │
│ │ user2: how much?        │   │
│ │ user3: 😍😍             │   │
│ └─────────────────────────┘   │
│                                │
│ [🛍️ Shop product name $XX]    │  ← Shopping bar: 0-80% width, 85-92% height
│ [Type a comment...        ] 🎁│  ← Input bar: full width, 92-100% height
└────────────────────────────────┘
```

**Safe zone for content placement:**
- **Best area:** Center 20-80% width, 10-50% height (above chat, below top bar)
- **Avoid:** Bottom 40% (chat + shop bar + input), right 15% (action rail), top 5% (LIVE badge)
- **Product overlay ideal zone:** 60-90% X, 5-40% Y (top-right quadrant, avoiding action rail)

**Implementation:** Semi-transparent PNG overlay at 60% opacity showing:
- Grey zones where TikTok UI covers content
- Dashed green lines showing the "safe zone" for placing content
- Stored at `public/tiktok-chrome.png` (1080×1920 source, scaled to 450×800 canvas)
