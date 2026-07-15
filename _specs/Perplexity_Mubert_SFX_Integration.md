# Perplexity — Mubert Music + SFX Integration

**Read RULES.md first. Mubert provides AI-generated, royalty-free background music via API. SFX (sound effects) are generated as markers in the script and resolved during rendering.**

---

## CONTEXT — Why this matters NOW

As of July 25, 2025, TikTok BANNED copyrighted music on LIVE streams. Creators can ONLY use TikTok's Live Sound Library, royalty-free music, or AI-generated music. Mubert's AI-generated music is 100% safe for LIVE and recorded content across ALL platforms. This is a competitive advantage — our users get legal music automatically.

---

## PART 1 — Mubert API Integration

### 1.1 — Service wrapper

Create `backend/orchestrator/services/mubert.py`:

```python
"""
Mubert AI Music API wrapper.
Generates royalty-free background music tailored to cast mood and duration.
Docs: https://api.mubert.com
"""
import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

MUBERT_API_URL = "https://api.mubert.com/v2/RecordTrackTTM"

# Mood-to-tags mapping: block moods → Mubert tags
MOOD_TO_TAGS = {
    # High energy
    "excited": ["energetic", "pop", "upbeat", "bright"],
    "urgent": ["intense", "driving", "electronic", "fast"],
    "hype": ["trap", "bass", "energetic", "powerful"],
    
    # Medium energy
    "enthusiastic": ["pop", "upbeat", "cheerful", "positive"],
    "confident": ["corporate", "motivational", "modern", "confident"],
    "informative": ["ambient", "light", "electronic", "calm"],
    "trustworthy": ["corporate", "warm", "acoustic", "gentle"],
    
    # Low energy
    "calm": ["ambient", "chill", "relaxing", "soft"],
    "intimate": ["lofi", "acoustic", "intimate", "warm"],
    "mysterious": ["cinematic", "dark", "atmospheric", "mysterious"],
    "emotional": ["piano", "emotional", "cinematic", "slow"],
    
    # Specific use cases
    "product_demo": ["electronic", "light", "modern", "minimal"],
    "social_proof": ["uplifting", "positive", "warm", "acoustic"],
    "cta": ["energetic", "driving", "pop", "intense"],
    "unboxing": ["exciting", "pop", "bright", "cheerful"],
    "tutorial": ["lofi", "chill", "ambient", "focus"],
    "live_selling": ["pop", "energetic", "commercial", "upbeat"],
}

# Content type to default mood
CONTENT_TYPE_MOOD = {
    "product_showcase": "enthusiastic",
    "tutorial": "informative",
    "before_after": "emotional",
    "flash_sale": "urgent",
    "review": "confident",
    "storytime": "intimate",
    "live_selling": "live_selling",
}


class MubertService:
    
    def __init__(self):
        self.api_key = settings.MUBERT_API_KEY
        if not self.api_key:
            raise RuntimeError("MUBERT_API_KEY not configured")
    
    async def generate_track(
        self,
        duration_seconds: int,
        mood: str = "enthusiastic",
        intensity: str = "medium",      # low, medium, high
        tempo: str = "medium",           # slow, medium, fast
        custom_tags: list[str] = None,
    ) -> dict:
        """Generate a background music track.
        
        Returns: {"download_url": "https://...", "duration": 60, "tags": [...]}
        """
        tags = custom_tags or MOOD_TO_TAGS.get(mood, ["pop", "upbeat", "modern"])
        
        # Map intensity to Mubert's intensity param
        intensity_map = {"low": "low", "medium": "medium", "high": "high"}
        
        payload = {
            "method": "RecordTrackTTM",
            "params": {
                "pat": self.api_key,
                "duration": duration_seconds,
                "tags": tags,
                "mode": "track",
                "intensity": intensity_map.get(intensity, "medium"),
                "format": "mp3",
            }
        }
        
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(MUBERT_API_URL, json=payload)
            resp.raise_for_status()
            result = resp.json()
        
        # Mubert returns a task — poll for completion
        if result.get("data", {}).get("tasks"):
            task = result["data"]["tasks"][0]
            download_url = task.get("download_link", "")
            
            if not download_url:
                # Task may need polling
                task_id = task.get("task_id")
                download_url = await self._poll_task(task_id)
            
            logger.info("Mubert track generated: %s (mood=%s, %ds)", 
                        download_url[:80], mood, duration_seconds)
            
            return {
                "download_url": download_url,
                "duration": duration_seconds,
                "mood": mood,
                "tags": tags,
            }
        
        raise RuntimeError(f"Mubert returned unexpected response: {result}")
    
    async def _poll_task(self, task_id: str, max_attempts: int = 30) -> str:
        """Poll Mubert for task completion (track generation is async)."""
        import asyncio
        
        for attempt in range(max_attempts):
            await asyncio.sleep(2)
            
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(MUBERT_API_URL, json={
                    "method": "TrackStatus",
                    "params": {
                        "pat": self.api_key,
                        "task_id": task_id,
                    }
                })
                result = resp.json()
            
            status = result.get("data", {}).get("tasks", [{}])[0]
            if status.get("download_link"):
                return status["download_link"]
            if status.get("error"):
                raise RuntimeError(f"Mubert task failed: {status['error']}")
        
        raise RuntimeError("Mubert task timed out")
    
    async def generate_for_cast(self, cast) -> dict:
        """Generate a background music track matched to a cast's overall mood.
        
        Analyzes block types and moods to pick the best overall music style.
        """
        # Determine dominant mood from blocks
        block_moods = [b.mood or "enthusiastic" for b in cast.blocks if b.is_active]
        
        # Pick the most common mood, weighted by duration
        from collections import Counter
        mood_counts = Counter(block_moods)
        dominant_mood = mood_counts.most_common(1)[0][0] if mood_counts else "enthusiastic"
        
        # Calculate total cast duration
        total_duration = sum(
            b.estimated_duration_seconds or 15 for b in cast.blocks if b.is_active
        )
        
        # Determine intensity from block types
        has_cta = any(b.block_type == "cta" for b in cast.blocks)
        has_hook = any(b.block_type == "hook" for b in cast.blocks)
        intensity = "high" if (has_cta and has_hook) else "medium"
        
        return await self.generate_track(
            duration_seconds=total_duration,
            mood=dominant_mood,
            intensity=intensity,
        )
```

### 1.2 — Config

Add to `config.py` Settings class:
```python
MUBERT_API_KEY: str = ""
PEXELS_API_KEY: str = ""  # also needed for stock media search
```

Add to `.env`:
```
MUBERT_API_KEY=your_mubert_api_key
PEXELS_API_KEY=your_pexels_api_key
```

### 1.3 — Auto-generate music after cast creation

In the cast generation pipeline, after scripts are generated and before audio phase:

```python
# In generate_cast_task or after outline generation:
async def auto_generate_background_music(cast):
    """Generate and attach background music to the cast."""
    try:
        mubert = MubertService()
        track = await mubert.generate_for_cast(cast)
        
        # Download and upload to R2
        async with httpx.AsyncClient() as client:
            resp = await client.get(track["download_url"])
            music_bytes = resp.content
        
        music_key = f"music/{cast.id}/background.mp3"
        await r2.upload_bytes(music_bytes, music_key, "audio/mpeg")
        
        # Store on cast
        cast.background_music_url = r2.get_public_url(music_key)
        cast.background_music_mood = track["mood"]
        cast.background_music_tags = track["tags"]
        
        logger.info("Background music generated for cast %s: mood=%s", cast.id, track["mood"])
    except Exception as e:
        logger.warning("Failed to generate background music: %s", e)
        # Non-fatal — cast works without music
```

### 1.4 — Place music on the timeline

In `castToEditorStarterTimeline()`, if the cast has `background_music_url`, create an audio track item spanning the full duration:

```typescript
if (cast.background_music_url) {
  const musicAssetId = `asset_music_bg`;
  const musicItemId = `item_music_bg`;
  
  assets[musicAssetId] = {
    type: "audio",
    src: cast.background_music_url,
    duration: totalDuration,
  };
  
  items[musicItemId] = {
    type: "audio",
    assetId: musicAssetId,
    s: 0,
    e: totalDuration,
    props: {
      src: cast.background_music_url,
      volume: 0.15,  // low volume — under voiceover
    },
    metadata: {
      track_type: "background_music",
      auto_duck: true,  // reduce volume when voice is active
    },
  };
  
  // Add to a dedicated music track (lowest track)
  musicTrackItemIds.push(musicItemId);
}
```

### 1.5 — Audio ducking in FFmpeg render

During the final compose, background music should be QUIETER when voiceover is speaking:

```python
# In FFmpeg composition:
# Use sidechaincompress or volume automation
# Simple approach: set music to 15% volume when voice is active, 40% when silent

# More sophisticated: FFmpeg sidechain compression
# -filter_complex "[0:a][1:a]sidechaincompress=threshold=0.02:ratio=6:attack=200:release=1000[music_ducked]"
```

---

## PART 2 — SFX (Sound Effects) in Script Generation

### 2.1 — SFX markers in the script

SFX are NOT music — they're short sound effects (whoosh, ding, pop, cash register) that punctuate specific moments. The LLM generates SFX markers in the script, and the render pipeline resolves them to actual audio files.

Add SFX instructions to the per-block script generation prompt in `ai_prompts.py`:

```python
SFX_INSTRUCTIONS = """
SOUND EFFECTS:
Insert [sfx:NAME] markers at moments that benefit from audio punctuation.
These play OVER the voice, not instead of it.

Available SFX:
  [sfx:whoosh]         — transition, swipe, reveal
  [sfx:pop]            — item appearing, text popup
  [sfx:ding]           — notification, purchase, achievement
  [sfx:cash_register]  — purchase moment, price reveal, deal
  [sfx:sparkle]        — product highlight, premium moment
  [sfx:record_scratch] — pattern interrupt, "wait what?"
  [sfx:swoosh_up]      — energy rise, excitement building
  [sfx:swoosh_down]    — energy drop, disappointment, before/after "before"
  [sfx:notification]   — social proof, "@user just purchased"
  [sfx:timer_tick]     — countdown, urgency
  [sfx:click]          — button press, link tap
  [sfx:drumroll]       — reveal anticipation
  [sfx:applause]       — celebration, social proof
  [sfx:camera_shutter] — photo moment, before/after capture

RULES:
- Max 2-3 SFX per 15-second block
- SFX enhance, they don't replace the voice
- Place [sfx:] BEFORE the word it accompanies
- Most common patterns:
  Hook: "[sfx:record_scratch] Wait, did you just say $7?"
  Product reveal: "[sfx:sparkle] Look at this texture"
  Price reveal: "[sfx:cash_register] And it's only $24.99"
  CTA: "[sfx:notification] 47 people just added to cart"
  Transition: "[sfx:whoosh]" at block boundaries

EXAMPLE:
"[sfx:record_scratch] Okay wait. (excited) This $7 cream [sfx:sparkle] 
just beat a $200 brand in every single test. [pause] 
I'm not even kidding. [sfx:cash_register] And right now it's 40% off."
"""
```

### 2.2 — SFX library (bundled audio files)

Store a library of short SFX clips in R2. Each clip is 0.3-1.5 seconds:

```python
SFX_LIBRARY = {
    "whoosh": "sfx/whoosh.mp3",
    "pop": "sfx/pop.mp3",
    "ding": "sfx/ding.mp3",
    "cash_register": "sfx/cash_register.mp3",
    "sparkle": "sfx/sparkle.mp3",
    "record_scratch": "sfx/record_scratch.mp3",
    "swoosh_up": "sfx/swoosh_up.mp3",
    "swoosh_down": "sfx/swoosh_down.mp3",
    "notification": "sfx/notification.mp3",
    "timer_tick": "sfx/timer_tick.mp3",
    "click": "sfx/click.mp3",
    "drumroll": "sfx/drumroll.mp3",
    "applause": "sfx/applause.mp3",
    "camera_shutter": "sfx/camera_shutter.mp3",
}
```

**Source these SFX from Freesound.org (CC0 license) or generate them with Mubert's SFX capabilities.** Upload to R2 at `s3://luminacast/sfx/`.

### 2.3 — SFX extraction during TTS

When Fish Speech generates TTS audio, the `[sfx:NAME]` markers should be STRIPPED from the text before sending to TTS (Fish Speech doesn't understand them). Instead, their positions are recorded:

```python
import re

def extract_sfx_markers(script_text: str) -> tuple[str, list[dict]]:
    """Extract SFX markers from script, return clean text + SFX timeline."""
    sfx_markers = []
    clean_text = script_text
    
    # Find all [sfx:NAME] markers
    pattern = r'\[sfx:(\w+)\]'
    
    # Calculate word position for timing
    words_before = []
    for match in re.finditer(pattern, script_text):
        sfx_name = match.group(1)
        # Count words before this marker
        text_before = script_text[:match.start()]
        word_count = len(text_before.split())
        sfx_markers.append({
            "name": sfx_name,
            "word_position": word_count,
            "char_position": match.start(),
        })
    
    # Remove markers from text for TTS
    clean_text = re.sub(pattern, '', script_text).strip()
    clean_text = re.sub(r'\s+', ' ', clean_text)  # collapse double spaces
    
    return clean_text, sfx_markers


def resolve_sfx_timing(sfx_markers: list, whisperx_words: list) -> list[dict]:
    """After TTS + WhisperX transcription, resolve SFX to exact timestamps."""
    resolved = []
    
    for marker in sfx_markers:
        word_pos = marker["word_position"]
        
        # Find the timestamp of the word at this position
        if word_pos < len(whisperx_words):
            word = whisperx_words[word_pos]
            timestamp = word["start"]
        elif whisperx_words:
            timestamp = whisperx_words[-1]["end"]
        else:
            timestamp = 0
        
        resolved.append({
            "name": marker["name"],
            "timestamp": timestamp,
            "audio_url": f"https://media.luminacast.com/sfx/{marker['name']}.mp3",
        })
    
    return resolved
```

### 2.4 — SFX mixing in FFmpeg render

During final composition, overlay SFX at the correct timestamps:

```python
# Build FFmpeg filter for SFX mixing
sfx_inputs = []
sfx_filters = []

for i, sfx in enumerate(resolved_sfx):
    sfx_input_idx = len(base_inputs) + i
    sfx_inputs.extend(["-i", sfx["audio_path"]])
    
    # Delay the SFX to the correct timestamp
    delay_ms = int(sfx["timestamp"] * 1000)
    sfx_filters.append(
        f"[{sfx_input_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.7[sfx{i}]"
    )

# Mix all: voice + music + all SFX
if sfx_filters:
    mix_inputs = "[voice][music_ducked]" + "".join(f"[sfx{i}]" for i in range(len(resolved_sfx)))
    filter_complex = ";".join(sfx_filters) + f";{mix_inputs}amix=inputs={2 + len(resolved_sfx)}:duration=first[final_audio]"
```

---

## PART 3 — Music in the Editor (Music Tab)

### 3.1 — Music page / tab

The existing "Music" sidebar item should show:

```
┌─────────────────────────────────────────────────────────────┐
│  MUSIC                                                      │
│                                                             │
│  [AI Generated]  [Uploaded]  [SFX Library]                  │
│                                                             │
│  ── AI GENERATED ──────────────────────────────────────     │
│                                                             │
│  Generate background music that matches your cast's mood:   │
│                                                             │
│  Mood: [Energetic ▼]  Intensity: [High ▼]  Duration: auto  │
│  [🎵 Generate Track]                                        │
│                                                             │
│  ┌─ Generated tracks ─────────────────────────────────┐    │
│  │  🎵 Track 1 · Energetic Pop · 65s                   │    │
│  │  ▶ ████████████░░░░ 0:23/1:05  [Use] [Regenerate]  │    │
│  │                                                      │    │
│  │  🎵 Track 2 · Chill Ambient · 65s                   │    │
│  │  ▶ ░░░░░░░░░░░░░░░░ 0:00/1:05  [Use] [Delete]      │    │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  ── SFX LIBRARY ───────────────────────────────────────     │
│                                                             │
│  [🔊 whoosh]  [🔔 ding]  [💰 cash register]  [✨ sparkle] │
│  [🎬 swoosh]  [📸 shutter]  [👏 applause]  [⏱ tick]      │
│  [🔴 record scratch]  [📳 notification]  [🥁 drumroll]    │
│                                                             │
│  Click to preview · Drag to timeline                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 — Auto-music in cast generation

When a cast is created, background music is auto-generated and placed on the timeline. The user sees it as a track they can:
- Mute/unmute
- Replace (regenerate with different mood)
- Remove entirely
- Adjust volume

### 3.3 — SFX on timeline

SFX extracted from the script appear as small markers on a dedicated SFX track:

```
Track 1: [🎵 Background music ──────────────────────────────]
Track 2: [sfx:whoosh] · · · [sfx:sparkle] · · [sfx:cash_register]
Track 3: [Voice — Block 1] [Voice — Block 2] [Voice — Block 3]
Track 4: [AI Avatar — Block 1] [AI Avatar — Block 2] [AI Avatar — Block 3]
```

Each SFX marker is a tiny item the user can drag, delete, or click to preview.

---

## PART 4 — Music in Live Streams

### 4.1 — Live stream music is MANDATORY (and legal)

Since TikTok banned copyrighted music on LIVE (July 2025), our Mubert-generated music is a MAJOR selling point. The live stream compositor should:

1. Auto-generate a long background track (2-4 hours) via Mubert with `mode: "stream"` or by concatenating multiple tracks
2. Play it at low volume (10-15%) throughout the entire stream
3. Slightly increase volume during product transition cards (3-5 seconds of music-only between products)
4. Duck under voiceover automatically

```python
# For live streams: generate a long ambient track
async def generate_live_music(duration_hours: int = 2, mood: str = "live_selling"):
    mubert = MubertService()
    
    # Generate in 30-minute chunks and concatenate
    chunks = []
    for i in range(duration_hours * 2):
        track = await mubert.generate_track(
            duration_seconds=1800,  # 30 min
            mood=mood,
            intensity="low",  # background, never overpowering
        )
        chunks.append(track["download_url"])
    
    return chunks  # concatenated in the RTMP compositor
```

### 4.2 — Live SFX

During live streams, the orchestrator can trigger SFX in real-time:

```python
# In the live orchestrator:
async def on_purchase(event):
    # Play cash register SFX
    await compositor.play_sfx("cash_register")
    # Then speak the thank-you
    await inject_reactive_voiceover(event)

async def on_product_transition():
    # Play whoosh SFX
    await compositor.play_sfx("whoosh")
    # Show product card
    await compositor.show_transition_card()
```

---

## PART 5 — Platform-Specific Music Rules

The system should know these rules and enforce them automatically:

```python
PLATFORM_MUSIC_RULES = {
    "tiktok": {
        "recorded": {
            "mubert_allowed": True,
            "copyrighted_allowed": True,   # from TikTok Sounds library
            "commercial_music_library": True,  # for Shop/commercial
            "max_volume": 1.0,
        },
        "live": {
            "mubert_allowed": True,        # AI-generated = safe
            "copyrighted_allowed": False,   # BANNED since July 2025
            "note": "Only TikTok Live Sound Library, royalty-free, or AI-generated",
        },
    },
    "instagram": {
        "recorded": {"mubert_allowed": True, "copyrighted_allowed": True},
        "live": {"mubert_allowed": True, "copyrighted_allowed": False},
    },
    "youtube": {
        "recorded": {"mubert_allowed": True, "copyrighted_allowed": False},  # Content ID risk
        "live": {"mubert_allowed": True, "copyrighted_allowed": False},
    },
    "facebook": {
        "recorded": {"mubert_allowed": True, "copyrighted_allowed": True},
        "live": {"mubert_allowed": True, "copyrighted_allowed": False},
    },
    "linkedin": {
        "recorded": {"mubert_allowed": True, "music_recommended": False},  # professional = often no music
        "note": "Music can be distracting on LinkedIn — use sparingly",
    },
    "pinterest": {
        "recorded": {"mubert_allowed": True},
    },
}
```

When the user publishes to LinkedIn, show a suggestion: "LinkedIn posts often perform better without background music. Remove music for this platform?"

---

## Implementation order

1. **Mubert service wrapper** + config (1 hour)
2. **Auto-generate music after cast creation** (1 hour)
3. **Place music on timeline** in editorStarterMapping (1 hour)
4. **SFX instructions in LLM prompt** — add to ai_prompts.py (10 min)
5. **SFX extraction from script** — strip markers, record positions (1 hour)
6. **SFX timing resolution** after WhisperX (1 hour)
7. **SFX + music mixing in FFmpeg** render (2 hours)
8. **Music page UI** — generate, preview, use, SFX library (3 hours)
9. **Audio ducking** in render (1 hour)
10. **Live stream music** integration (2 hours)
11. **Source SFX clips** from Freesound CC0 and upload to R2 (1 hour)

---

## SFX clips to source (download from Freesound.org CC0):

Search Freesound.org for these CC0 clips and upload to `s3://luminacast/sfx/`:

1. `whoosh.mp3` — search "swoosh transition short"
2. `pop.mp3` — search "bubble pop cartoon"
3. `ding.mp3` — search "notification bell short"
4. `cash_register.mp3` — search "cash register ka-ching"
5. `sparkle.mp3` — search "magic sparkle shine"
6. `record_scratch.mp3` — search "vinyl record scratch"
7. `swoosh_up.mp3` — search "swoosh rise ascending"
8. `swoosh_down.mp3` — search "swoosh fall descending"
9. `notification.mp3` — search "phone notification ding"
10. `timer_tick.mp3` — search "clock tick single"
11. `click.mp3` — search "button click UI"
12. `drumroll.mp3` — search "drumroll short"
13. `applause.mp3` — search "applause cheering short"
14. `camera_shutter.mp3` — search "camera shutter click"

All clips should be:
- CC0 or Public Domain license
- Under 2 seconds
- Normalized to -3dB
- MP3 format, 44.1kHz

---

## Do NOT touch

- Fish Speech TTS (SFX markers are stripped before TTS, not sent to Fish Speech)
- InfiniteTalk rendering (music is mixed in FFmpeg, not in the video generation)
- Existing render pipeline structure (just add music + SFX mixing at the compose step)
- Go Live architecture (live music is a layer on top, not a replacement)
