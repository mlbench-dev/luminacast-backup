# Perplexity — Caption UX Overhaul + Bonded Tracks

**Read RULES.md first. Each fix = own commit + deploy + test.**

**CRITICAL CONTEXT: The editor is built on Remotion v4.0.433 (Editor Starter paid template).** Remotion's core promise is preview = render. The same React components that render in the browser should generate the final MP4 via `npx remotion render`. We have `@remotion/captions` installed but barely used. The current render pipeline uses a SEPARATE FFmpeg compose step — that's why preview captions don't match rendered captions. They're two different engines.

**For NOW:** Keep FFmpeg render pipeline (it works for InfiniteTalk video compositing). But make FFmpeg read the SAME caption style settings that the preview uses. Caption presets should be built as Remotion-native components using `@remotion/captions` so they render perfectly in preview AND are ready for a future Remotion SSR render path.

**The `@remotion/captions` package supports:** word-level timestamps, custom per-word styling, animation sequences, karaoke highlighting, `<Caption>` components with `startFrom`/`endAt` timing. USE IT instead of building custom caption rendering.

---

## FIX 1 — Captions: use @remotion/captions properly, sentence-level grouping

### Problem
Track 2 shows individual words/letters as separate timeline items (`l`, `w...`, `B`, `fi...`). This is because WhisperX returns word-level timestamps and each word becomes its own timeline element. Meanwhile, `@remotion/captions` v4.0.433 is installed but barely used — it has built-in support for word grouping, page-level display, and animation.

### Fix: Use @remotion/captions components

Check how `captions-layer.tsx` and `caption-page.tsx` currently work:

```bash
cat frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/captions-layer.tsx
cat frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/caption-page.tsx
```

The `@remotion/captions` package provides:
- `createTikTokStyleCaptions()` — groups words into pages automatically
- `<CaptionPage>` — renders a page of words with timing
- Word-level highlighting (active word changes color as spoken)

Replace the current word-per-item approach with `@remotion/captions` page grouping:

```tsx
import { createTikTokStyleCaptions } from "@remotion/captions";

// Convert WhisperX words to @remotion/captions format
const captionPages = createTikTokStyleCaptions({
  captions: whisperXWords.map(w => ({
    text: w.word,
    startMs: w.start * 1000,
    endMs: w.end * 1000,
    confidence: w.confidence || 1,
  })),
  combineTokensWithinMilliseconds: 3500,  // group into 3.5s max pages
});

// Each page becomes ONE timeline item (not one per word)
// The page internally handles word-by-word highlighting
```

### Timeline display after fix

Each caption segment on the timeline shows the full phrase:
```
Track: [Alright guys, listen up!] [I've got two amazing...] [First, we've got the...]
```

A 60-second cast should have ~12-20 caption segments, not 150+ word fragments.

### The caption Remotion component

The caption layer should render using Remotion's Sequence + useCurrentFrame:

```tsx
// captions-layer.tsx — rewrite to use @remotion/captions
import { useCurrentFrame, useVideoConfig, Sequence } from "remotion";

const CaptionsLayer: React.FC<{ pages: CaptionPage[]; style: CaptionPreset }> = ({ pages, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTimeMs = (frame / fps) * 1000;
  
  // Find the active page
  const activePage = pages.find(
    p => currentTimeMs >= p.startMs && currentTimeMs < p.endMs
  );
  
  if (!activePage) return null;
  
  return (
    <div style={{
      position: "absolute",
      bottom: style.position === "bottom_center" ? "15%" : "50%",
      left: "50%",
      transform: "translateX(-50%)",
      maxWidth: style.maxWidth || "85%",
      textAlign: "center",
    }}>
      {activePage.tokens.map((token, i) => {
        const isActive = currentTimeMs >= token.startMs && currentTimeMs < token.endMs;
        return (
          <span key={i} style={{
            fontFamily: style.fontFamily,
            fontWeight: style.fontWeight,
            fontSize: style.fontSize,
            color: isActive && style.activeWordColor ? style.activeWordColor : style.color,
            WebkitTextStroke: style.strokeWidth ? `${style.strokeWidth}px ${style.strokeColor}` : undefined,
            textShadow: style.shadow,
            padding: "0 2px",
            transition: "color 0.1s, transform 0.1s",
            transform: isActive && style.animation === "word_bounce" ? "scale(1.15)" : "scale(1)",
            display: "inline",
          }}>
            {token.text}{" "}
          </span>
        );
      })}
    </div>
  );
};
```

This gives you:
- **Word-by-word karaoke highlighting** (active word changes color/scale)
- **Sentence-level display** (only current page shows)
- **Pixel-perfect preview** (React renders identically in preview and future Remotion SSR)
- **Style presets applied uniformly** (one preset object drives everything)

---

## FIX 2 — Caption presets: global style picker

### Problem
Caption styling is buried in a dropdown inspector that only appears when you click a specific caption element. No presets, no "apply to all" option.

### Fix: Caption style bar at track level

When the Captions toggle is ON, show a **caption style bar** above the caption track (or as a persistent panel):

```
┌─────────────────────────────────────────────────────────────────┐
│  CAPTION STYLE                                                  │
│                                                                 │
│  Preset: [Modern Pop ▼]                                        │
│                                                                 │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│
│  │ Modern  │ │ Bold    │ │ Minimal │ │ Neon    │ │ Karaoke ││
│  │  Pop    │ │ Impact  │ │ Clean   │ │ Glow    │ │ Bounce  ││
│  │         │ │         │ │         │ │         │ │         ││
│  │ Example │ │ EXAMPLE │ │ example │ │ example │ │ example ││
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘│
│                                                                 │
│  [Apply to all captions]                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Preset definitions

Build these as React style objects that drive BOTH the Remotion preview component AND the FFmpeg drawtext fallback. Each preset has two representations:

```typescript
interface CaptionPreset {
  name: string;
  
  // ── Shared properties (used by both Remotion + FFmpeg) ──
  fontFamily: string;
  fontWeight: number;
  fontSize: number;
  color: string;
  strokeColor: string;
  strokeWidth: number;
  position: "bottom_center" | "center" | "top_center";
  maxWidth: string;
  
  // ── Remotion-only properties (rich features for preview) ──
  background?: string;
  backgroundPadding?: { x: number; y: number };
  backgroundRadius?: number;
  animation?: "none" | "fade_up" | "scale_in" | "glow_pulse" | "word_bounce";
  activeWordColor?: string;   // for karaoke highlighting
  shadow?: string;
  textTransform?: string;
  
  // ── FFmpeg fallback properties (derived from above for render) ──
  // These are auto-calculated, not manually set:
  // ffmpegFontFile, ffmpegFontColor, ffmpegBorderW, ffmpegBorderColor, ffmpegY
}

const CAPTION_PRESETS: Record<string, CaptionPreset> = {
  modern_pop: {
    name: "Modern Pop",
    fontFamily: "Inter",
    fontWeight: 800,
    fontSize: 42,
    color: "#FFFFFF",
    strokeColor: "#000000",
    strokeWidth: 3,
    background: "rgba(0,0,0,0.6)",
    backgroundPadding: { x: 16, y: 8 },
    backgroundRadius: 12,
    position: "bottom_center",  // 15% from bottom
    animation: "fade_up",
    maxWidth: "85%",
  },
  bold_impact: {
    name: "Bold Impact",
    fontFamily: "Inter",
    fontWeight: 900,
    fontSize: 56,
    color: "#FFFFFF",
    strokeColor: "#000000",
    strokeWidth: 5,
    background: "none",
    position: "center",
    animation: "scale_in",
    maxWidth: "90%",
    textTransform: "uppercase",
  },
  minimal_clean: {
    name: "Minimal Clean",
    fontFamily: "Inter",
    fontWeight: 500,
    fontSize: 32,
    color: "#FFFFFF",
    strokeColor: "none",
    background: "none",
    position: "bottom_center",
    animation: "none",
    maxWidth: "80%",
    shadow: "0 2px 8px rgba(0,0,0,0.8)",
  },
  neon_glow: {
    name: "Neon Glow",
    fontFamily: "Inter",
    fontWeight: 700,
    fontSize: 44,
    color: "#00FF88",
    strokeColor: "#000000",
    strokeWidth: 2,
    background: "none",
    position: "bottom_center",
    animation: "glow_pulse",
    maxWidth: "85%",
    shadow: "0 0 20px rgba(0,255,136,0.5)",
  },
  karaoke_bounce: {
    name: "Karaoke Bounce",
    fontFamily: "Inter",
    fontWeight: 800,
    fontSize: 48,
    color: "#FFFFFF",
    activeWordColor: "#FFD700",  // highlighted word turns gold
    strokeColor: "#000000",
    strokeWidth: 3,
    background: "none",
    position: "center",
    animation: "word_bounce",  // each word bounces in as spoken
    maxWidth: "90%",
  },
};
```

### "Apply to all" behavior

When user selects a preset and clicks "Apply to all captions":
```typescript
// Update ALL caption items in the timeline with the preset style
const captionTrack = tracks.find(t => t.type === "caption");
for (const itemId of captionTrack.itemIds) {
  const item = items[itemId];
  item.props = {
    ...item.props,
    ...preset,  // font, size, color, stroke, background, animation
  };
}
```

Individual caption items can still be overridden by clicking them and editing in the inspector.

---

## FIX 3 — Preview must match render (two-engine problem)

### Problem
The preview canvas renders captions using **Remotion React components** (browser font rendering, CSS styling). The final MP4 render uses a **separate FFmpeg `drawtext` filter** with potentially different font, size, position, and color. These are two completely different rendering engines — they will NEVER match perfectly.

### Root cause
The codebase has Remotion v4.0.433 installed — which can render MP4s directly via `npx remotion render`. But the render pipeline uses FFmpeg instead (because InfiniteTalk outputs video clips that need compositing). The captions are applied during FFmpeg composition, not by Remotion.

### Fix (Phase A — immediate): Make FFmpeg read Remotion's caption style

Store the active caption preset on the cast/timeline as JSON. When FFmpeg composes the final video, it reads the SAME preset values:

```python
# In cast_render.py, during FFmpeg composition:
# Read caption style from the timeline data
caption_style = timeline_data.get("captionStyle", {})
font_family = caption_style.get("fontFamily", "Inter")
font_size = caption_style.get("fontSize", 42)
font_color = caption_style.get("color", "white")
stroke_color = caption_style.get("strokeColor", "black")
stroke_width = caption_style.get("strokeWidth", 3)
position_y = caption_style.get("positionY", 0.85)  # 85% from top = 15% from bottom

# Build FFmpeg drawtext filter PER caption segment:
for seg in caption_segments:
    drawtext_filters.append(
        f"drawtext=text='{seg['text']}'"
        f":fontfile=/usr/share/fonts/truetype/inter/Inter-Bold.ttf"
        f":fontsize={font_size}"
        f":fontcolor={font_color}"
        f":borderw={stroke_width}"
        f":bordercolor={stroke_color}"
        f":x=(w-text_w)/2"
        f":y=h*{position_y}"
        f":enable='between(t,{seg['start']},{seg['end']})'"
    )
```

**IMPORTANT:** Bundle the Inter font in the Docker containers:

```bash
# In orchestrator Dockerfile AND GPU worker:
RUN apt-get update && apt-get install -y fonts-inter || \
    (mkdir -p /usr/share/fonts/truetype/inter && \
     wget -O /tmp/inter.zip "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip" && \
     unzip /tmp/inter.zip -d /tmp/inter && \
     cp /tmp/inter/Inter-*.ttf /usr/share/fonts/truetype/inter/ && \
     fc-cache -fv)
```

This won't be pixel-perfect (CSS rendering ≠ FFmpeg drawtext) but it will be CLOSE — same font, same size, same color, same position.

### Fix (Phase B — future): Use Remotion SSR for caption overlay

The proper long-term fix: after InfiniteTalk bakes the avatar video blocks and FFmpeg concatenates them, use Remotion to OVERLAY captions on the composed video:

```bash
# Compose video without captions (FFmpeg — existing pipeline)
ffmpeg ... -o composed_no_captions.mp4

# Overlay captions using Remotion SSR (pixel-perfect match to preview)
npx remotion render CaptionOverlay \
  --props='{"videoSrc": "composed_no_captions.mp4", "captions": [...], "style": {...}}' \
  --output final_with_captions.mp4
```

This guarantees preview = render for captions. The InfiniteTalk blocks are still composed by FFmpeg (Remotion can't do that), but captions are rendered by the same React components the user sees in the editor.

**Do Phase B later — Phase A is sufficient for now.**

---

## FIX 4 — Remove duplicate caption from audio inspector

### Problem
When a voice block is selected, the right panel shows "Captions → Caption audio" section. This is confusing — captions are a separate track, not a property of the audio block.

### Fix
Remove the "Captions" section from the audio/voice block inspector. Captions are managed exclusively via:
1. The Captions toggle in the toolbar (show/hide caption track)
2. The caption style bar (presets, apply to all)
3. Clicking individual caption items on the timeline (per-item override)

```bash
grep -rn "Caption.*audio\|caption.*section\|Captions.*collapse" \
  frontend/companion-app/src/components/cast-builder/editor-starter/ \
  --include="*.tsx" | head -10
```

Find and remove the "Captions" collapsible section from the audio/voice block inspector panel.

---

## FIX 5 — Bonded tracks: avatar video + voice audio visually linked

### Problem
"AI Avatar — Block 1" (track 5) and "Voice — Block 1" (track 4) are separate, independent tracks. Resizing one does NOT resize the other. They look unrelated. The user must know they're bonded by the naming convention alone.

### Fix: Visual bonding + synchronized resize

### 5.1 — Visual indicator

Bonded tracks (video + audio of the same block) should have a visual connector:

```tsx
// Each bonded pair gets:
// 1. Same color tint (they already have this — both purple/blue)
// 2. A thin connecting line or bracket between the two tracks
// 3. A small chain-link icon 🔗 on the left side

// When one is selected, the other highlights too
const bondedPairId = item.metadata?.bonded_pair_id;
const isBondedHighlight = selectedItem?.metadata?.bonded_pair_id === bondedPairId;

<div className={cn(
  "timeline-item",
  isSelected && "ring-2 ring-accent",
  isBondedHighlight && !isSelected && "ring-1 ring-accent/50 bg-accent/5",
)}>
```

### 5.2 — Synchronized resize

When the user drags the edge of a bonded video item to resize it, the bonded audio item resizes too (and vice versa):

```typescript
// In the timeline resize handler:
const handleItemResize = (itemId: string, newStart: number, newEnd: number) => {
  const item = items[itemId];
  const bondedPairId = item.metadata?.bonded_pair_id;
  
  // Resize this item
  updateItem(itemId, { s: newStart, e: newEnd });
  
  // Resize bonded pair
  if (bondedPairId) {
    const bondedItem = Object.values(items).find(
      i => i.id !== itemId && i.metadata?.bonded_pair_id === bondedPairId
    );
    if (bondedItem) {
      updateItem(bondedItem.id, { s: newStart, e: newEnd });
    }
  }
};
```

### 5.3 — Synchronized move

Dragging a bonded item horizontally moves both:

```typescript
const handleItemMove = (itemId: string, newStart: number) => {
  const item = items[itemId];
  const duration = item.e - item.s;
  const newEnd = newStart + duration;
  
  updateItem(itemId, { s: newStart, e: newEnd });
  
  // Move bonded pair
  const bondedPairId = item.metadata?.bonded_pair_id;
  if (bondedPairId) {
    const bondedItem = Object.values(items).find(
      i => i.id !== itemId && i.metadata?.bonded_pair_id === bondedPairId
    );
    if (bondedItem) {
      const bondedDuration = bondedItem.e - bondedItem.s;
      updateItem(bondedItem.id, { s: newStart, e: newStart + bondedDuration });
    }
  }
};
```

### 5.4 — Bonded pair metadata

In `castToEditorStarterTimeline`, when creating the video + audio items for a block, set a shared `bonded_pair_id`:

```typescript
const bondedPairId = `bond_${block.id}`;

// Video item
const videoItem = {
  ...standardImageItem,
  metadata: {
    block_id: block.id,
    bonded: true,
    bonded_pair_id: bondedPairId,  // ← links to audio
    // ...
  },
};

// Audio item
const audioItem = {
  ...standardAudioItem,
  metadata: {
    block_id: block.id,
    bonded: true,
    bonded_pair_id: bondedPairId,  // ← links to video
    // ...
  },
};
```

### 5.5 — Visual: collapsed bonded view (optional, nice-to-have)

A toggle to show bonded tracks as one combined row:

```
Normal view (2 rows):
  Track 5: [AI Avatar — Block 1] [AI Avatar — Block 2]
  Track 4: [Voice — Block 1]     [Voice — Block 2]

Collapsed view (1 row):
  Track 5: [🔗 Block 1 (video+audio)] [🔗 Block 2 (video+audio)]
```

The collapsed view shows the video thumbnail with a small audio waveform overlay. Double-click to expand back to two rows.

---

## FIX 6 — Captions and Gestures: STILL not merged into toolbar

I see from the screenshot that Captions and Gestures are STILL on a separate row from Safe zones / TikTok / Reels / Shorts / Layers. There's also a red "Delete" button still visible.

Move Captions and Gestures into the same row. Remove the red Delete button (it goes in the kebab menu). This has been requested FIVE times.

```
CURRENT (wrong):
  [Select][Undo][Redo] | Safe zones TikTok Reels Shorts | Layers     | — Fit +
                        Captions  Gestures  🗑 Delete

CORRECT:
  [Select][Undo][Redo] | Safe zones TikTok Reels Shorts | Layers | Captions Gestures | — Fit +
```

---

## Implementation order

**Step 0 — FIRST: Read the existing Remotion captions code:**
```bash
# Before changing ANYTHING, understand what's already built:
cat frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/captions-layer.tsx
cat frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/caption-page.tsx

# Check how captions are created from WhisperX:
grep -rn "caption\|whisper\|transcri" frontend/companion-app/src/lib/editorStarterMapping.ts | head -20

# Check @remotion/captions usage:
grep -rn "from '@remotion/captions'\|from \"@remotion/captions\"" frontend/companion-app/src/ --include="*.tsx" --include="*.ts" | head -10

# Check how captions are rendered in FFmpeg:
grep -rn "drawtext\|caption\|subtitle\|ass\|srt" backend/orchestrator/tasks/cast_render.py | head -10
```

**Do NOT rewrite anything until you understand the existing flow. You have `@remotion/captions` v4.0.433 installed — use its APIs, don't build custom grouping logic.**

1. Fix 1 — use `@remotion/captions` `createTikTokStyleCaptions()` for page grouping (highest impact)
2. Fix 5 — bonded tracks (fixes confusing video+audio disconnect)
3. Fix 2 — caption presets as Remotion-native style objects (makes captions beautiful)
4. Fix 3 Phase A — FFmpeg reads caption style from timeline data (approximate match)
5. Fix 4 — remove duplicate caption from audio inspector (cleanup)
6. Fix 6 — toolbar merge (5th time requesting)

---

## Do NOT touch

- Caption generation from WhisperX (keep word-level data, just GROUP it)
- Render pipeline (only change: read caption style from timeline data)
- Block categories / PIP / voiceover handling
- Go Live / Zernio integration (separate instructions)

---

## ADDENDUM — Captions Only Show for First Block + Preview ≠ Render

### Root Cause 1: `caption_words` only populated for block 1

In `editorStarterMapping.ts` line 437:
```typescript
const rawCaptionWords = variant?.caption_words;
```

`caption_words` comes from WhisperX transcription. Check the backend — WhisperX may only run on the first block's audio, or only the first variant stores the result.

**Debug on backend:**
```bash
# Check if caption_words exists for all variants or just the first
docker compose exec postgres psql -U luminacast -d luminacast -c "
SELECT b.position, v.id, 
  v.script_text IS NOT NULL as has_script,
  v.audio_key IS NOT NULL as has_audio,
  v.caption_words IS NOT NULL as has_caption_words,
  jsonb_array_length(v.caption_words::jsonb) as word_count
FROM blocks b
JOIN variants v ON v.block_id = b.id
WHERE b.cast_id = (SELECT id FROM casts ORDER BY updated_at DESC LIMIT 1)
  AND v.is_active = true
ORDER BY b.position;
"
```

If `has_caption_words` is `false` for blocks 2+, then WhisperX isn't running on those blocks. Fix:

```python
# In the audio generation task, after TTS generates audio for EACH block:
# Run WhisperX on each block's audio to generate caption_words

async def generate_block_audio(block, variant):
    # 1. Generate TTS audio via Fish Speech
    audio_url = await fish_speech.tts(variant.script_text, ...)
    
    # 2. Run WhisperX to get word-level timestamps
    caption_words = await whisperx.transcribe(audio_url)
    
    # 3. Store BOTH on the variant
    variant.audio_url = audio_url
    variant.caption_words = caption_words  # ← THIS MUST HAPPEN FOR EVERY BLOCK
    
    await db.commit()
```

If WhisperX is only called once on the concatenated audio (all blocks together), the word timestamps won't map correctly to individual blocks. **WhisperX must run PER BLOCK, not on the full cast audio.**

### Root Cause 2: Two caption systems — TextItem vs CaptionsLayer

The codebase has TWO caption rendering paths:

**Path A (currently used):** `editorStarterMapping.ts` creates `TextItem` objects (type: `"text"`) with 5-word chunks and puts them on a caption track. These render as plain text overlays on the canvas.

**Path B (exists but unused):** `CaptionsLayer` uses `@remotion/captions` with `createTikTokStyleCaptions` and `CaptionPage`. This is the PROPER Remotion caption system with word-highlighting, auto-paging, and animation.

**Fix:** Switch from Path A to Path B.

Instead of creating 5-word `TextItem` chunks, create ONE `CaptionsItem` per block that uses the Remotion caption system:

```typescript
// REPLACE the TextItem chunk loop (lines 454-500) with:
if (workingCaptions && workingCaptions.length > 0 && options.captionsEnabled !== false) {
  const capItemId = `cap_${block.id}`;
  const capAssetId = `asset_cap_${block.id}`;
  
  // Create a CaptionAsset with all words for this block
  assets[capAssetId] = {
    type: "caption",
    words: workingCaptions.map(w => ({
      text: w.word,
      startMs: (w.start + start) * 1000,  // offset by block start time
      endMs: (w.end + start) * 1000,
    })),
  };
  
  // Create a single CaptionsItem (NOT TextItem)
  items[capItemId] = {
    type: "captions",
    id: capItemId,
    assetId: capAssetId,
    from: secondsToFrames(start, fps),
    durationInFrames: Math.max(secondsToFrames(end - start, fps), 1),
    // Caption styling — read from cast.captionPreset or use defaults
    fontFamily: captionPreset?.fontFamily || "Inter",
    fontSize: captionPreset?.fontSize || 42,
    fontStyle: { variant: "normal", weight: captionPreset?.fontWeight?.toString() || "800" },
    color: captionPreset?.color || "#FFFFFF",
    highlightColor: captionPreset?.activeWordColor || "#FFD700",
    lineHeight: captionPreset?.lineHeight || 1.3,
    letterSpacing: 0,
    align: "center",
    direction: "ltr",
    strokeWidth: captionPreset?.strokeWidth || 3,
    strokeColor: captionPreset?.strokeColor || "#000000",
    top: canvas.height * 0.82,  // 82% from top = 18% from bottom
    left: canvas.width * 0.05,
    width: canvas.width * 0.9,
    height: canvas.height * 0.15,
    maxLines: 2,
    metadata: {
      block_id: block.id,
      track_type: "captions",
    },
  };
  
  captionTrackItemIds.push(capItemId);
}
```

Now `CaptionsLayer` handles the rendering — `createTikTokStyleCaptions` automatically groups words into pages, and `CaptionPage` renders each page with word-by-word highlighting. The preview uses the exact same React components that will render in future Remotion SSR.

### Root Cause 3: FFmpeg render must read the same caption data

In `cast_render.py`, the `_load_caption_overlays` function (line 256) must read the caption style from the timeline data, not use hardcoded values:

```python
async def _load_caption_overlays(timeline_data, caption_style):
    """Load captions from timeline and convert to FFmpeg drawtext filters."""
    
    # Read style from the preset (matches what the preview shows)
    font_family = caption_style.get("fontFamily", "Inter")
    font_size = caption_style.get("fontSize", 42)
    font_color = caption_style.get("color", "white")
    stroke_w = caption_style.get("strokeWidth", 3)
    stroke_color = caption_style.get("strokeColor", "black")
    position_y = caption_style.get("positionY", 0.82)
    
    drawtext_filters = []
    for item in timeline_data.get("items", {}).values():
        if item.get("metadata", {}).get("track_type") != "captions":
            continue
        
        asset = timeline_data["assets"].get(item.get("assetId", ""), {})
        words = asset.get("words", [])
        
        # Group words into pages (same logic as createTikTokStyleCaptions)
        pages = group_words_into_pages(words, max_duration_ms=3500)
        
        for page in pages:
            page_text = " ".join(w["text"] for w in page["words"])
            page_start = page["startMs"] / 1000
            page_end = page["endMs"] / 1000
            
            # Escape special chars for FFmpeg
            safe_text = page_text.replace("'", "\\'").replace(":", "\\:")
            
            drawtext_filters.append(
                f"drawtext=text='{safe_text}'"
                f":fontfile=/usr/share/fonts/truetype/inter/Inter-Bold.ttf"
                f":fontsize={font_size}"
                f":fontcolor={font_color}"
                f":borderw={stroke_w}"
                f":bordercolor={stroke_color}"
                f":x=(w-text_w)/2"
                f":y=h*{position_y}"
                f":enable='between(t,{page_start:.3f},{page_end:.3f})'"
            )
    
    return drawtext_filters
```

### Summary: what must happen for captions to work end-to-end

```
1. WhisperX transcribes EVERY block's audio (not just block 1)
   → each variant gets caption_words populated
   
2. editorStarterMapping creates CaptionsItem (not TextItem) per block
   → uses @remotion/captions CaptionAsset with word-level data
   → CaptionsLayer renders with createTikTokStyleCaptions + CaptionPage
   → preview shows styled, auto-paged, word-highlighted captions

3. User picks a caption preset (Modern Pop / Bold Impact / Karaoke etc.)
   → preset stored on cast as captionPreset JSON
   → drives BOTH preview (React) and render (FFmpeg)

4. FFmpeg render reads the SAME caption data + preset
   → drawtext filter uses same font, size, color, position
   → NOT pixel-perfect match (CSS ≠ FFmpeg) but CLOSE
   
5. Future: Remotion SSR overlays captions on FFmpeg-composed video
   → pixel-perfect match guaranteed
```
