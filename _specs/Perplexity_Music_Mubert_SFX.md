# Perplexity — Music Tab: Mubert Integration + SFX + ACE-Step Parking

**Read RULES.md first.**
**Reference document:** `Music_SFX_Knowledge_Base_v1.md` — contains all mood mappings, SFX library definitions, prosody tag rules, platform music permission rules, and catalog presets. The LLM prompts and Mubert service MUST read from this document, not hardcode values. When admins edit the knowledge base via `/preadmin`, the system picks up the changes.

---

## PART 1 — Mubert Service (Backend)

### 1.1 — Service wrapper

Create `backend/orchestrator/services/mubert.py`:

```python
"""
Mubert AI Music API wrapper.
Generates royalty-free background music tailored to cast mood and duration.

Mood-to-tags mapping lives in Music_SFX_Knowledge_Base_v1.md Section 1.
Load it from the prompt registry so admins can edit it.
"""
import logging
import httpx
from config import settings
from services.prompt_registry import get_prompt_data

logger = logging.getLogger(__name__)

MUBERT_API_URL = "https://api.mubert.com/v2/RecordTrackTTM"


def get_mood_tags(mood: str) -> list[str]:
    """Get Mubert tags for a mood. Reads from the knowledge base."""
    # Load from prompt registry / knowledge base
    # Fallback to hardcoded defaults if not found
    FALLBACK_TAGS = {
        "excited": ["energetic", "pop", "upbeat", "bright"],
        "urgent": ["intense", "driving", "electronic", "fast"],
        "enthusiastic": ["pop", "upbeat", "cheerful", "positive"],
        "calm": ["ambient", "chill", "relaxing", "soft"],
        "confident": ["corporate", "motivational", "modern"],
        "informative": ["ambient", "light", "electronic", "calm"],
    }
    return FALLBACK_TAGS.get(mood, ["pop", "upbeat", "modern"])


class MubertService:
    
    def __init__(self):
        self.api_key = settings.MUBERT_API_KEY
        if not self.api_key:
            raise RuntimeError("MUBERT_API_KEY not configured")
    
    async def generate_track(
        self,
        duration_seconds: int,
        mood: str = "enthusiastic",
        intensity: str = "medium",
        tempo: str = "medium",
        custom_tags: list[str] = None,
    ) -> dict:
        """Generate a background music track. Returns {download_url, duration, mood, tags}."""
        tags = custom_tags or get_mood_tags(mood)
        
        payload = {
            "method": "RecordTrackTTM",
            "params": {
                "pat": self.api_key,
                "duration": duration_seconds,
                "tags": tags,
                "mode": "track",
                "intensity": intensity,
                "format": "mp3",
            }
        }
        
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(MUBERT_API_URL, json=payload)
            resp.raise_for_status()
            result = resp.json()
        
        if result.get("data", {}).get("tasks"):
            task = result["data"]["tasks"][0]
            download_url = task.get("download_link", "")
            
            if not download_url:
                download_url = await self._poll_task(task.get("task_id"))
            
            return {
                "download_url": download_url,
                "duration": duration_seconds,
                "mood": mood,
                "tags": tags,
            }
        
        raise RuntimeError(f"Mubert unexpected response: {result}")
    
    async def _poll_task(self, task_id: str, max_attempts: int = 30) -> str:
        """Poll for track generation completion."""
        import asyncio
        for _ in range(max_attempts):
            await asyncio.sleep(2)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(MUBERT_API_URL, json={
                    "method": "TrackStatus",
                    "params": {"pat": self.api_key, "task_id": task_id}
                })
                status = resp.json().get("data", {}).get("tasks", [{}])[0]
                if status.get("download_link"):
                    return status["download_link"]
                if status.get("error"):
                    raise RuntimeError(f"Mubert failed: {status['error']}")
        raise RuntimeError("Mubert task timed out")
    
    async def generate_for_cast(self, cast) -> dict:
        """Auto-generate music matched to a cast's mood and duration."""
        from collections import Counter
        block_moods = [b.mood or "enthusiastic" for b in cast.blocks if getattr(b, 'is_active', True)]
        dominant_mood = Counter(block_moods).most_common(1)[0][0] if block_moods else "enthusiastic"
        total_duration = sum(
            getattr(b, 'estimated_duration_seconds', 15) for b in cast.blocks 
            if getattr(b, 'is_active', True)
        )
        has_cta = any(getattr(b, 'block_type', '') == "cta" for b in cast.blocks)
        intensity = "high" if has_cta else "medium"
        
        return await self.generate_track(total_duration, dominant_mood, intensity)
```

### 1.2 — Config

Add to `config.py`:
```python
MUBERT_API_KEY: str = ""
```

Add to `.env`:
```
MUBERT_API_KEY=your_key_here
```

### 1.3 — Auto-generate music after cast creation

In the cast generation pipeline (after scripts, before audio phase):

```python
async def auto_generate_background_music(cast):
    """Generate and attach background music. Non-fatal if fails."""
    try:
        mubert = MubertService()
        track = await mubert.generate_for_cast(cast)
        async with httpx.AsyncClient() as client:
            resp = await client.get(track["download_url"])
            music_bytes = resp.content
        music_key = f"music/{cast.id}/background.mp3"
        await r2.upload_bytes(music_bytes, music_key, "audio/mpeg")
        cast.background_music_url = r2.get_public_url(music_key)
        cast.background_music_mood = track["mood"]
        cast.background_music_tags = track["tags"]
    except Exception as e:
        logger.warning("Background music generation failed (non-fatal): %s", e)
```

### 1.4 — Music on timeline

In `castToEditorStarterTimeline()`, if cast has `background_music_url`, add an audio item on the lowest track spanning full duration. Volume: 0.15 (15%). Metadata: `{track_type: "background_music", auto_duck: true}`.

### 1.5 — Audio ducking in FFmpeg render

During composition, reduce music volume when voice is active:
```python
# FFmpeg sidechain compression: music ducks under voice
filter = "[voice][music]sidechaincompress=threshold=0.02:ratio=6:attack=200:release=1000[music_ducked]"
```

---

## PART 2 — SFX System

### 2.1 — Add SFX instructions to the LLM prompt

In `ai_prompts.py`, append to the `cast_script_generator` system prompt:

```python
# Load SFX rules from the knowledge base
SFX_PROMPT = """
SOUND EFFECTS:
Insert [sfx:NAME] markers at moments that benefit from audio punctuation.
These play OVER the voice, not instead of it.

Available: [sfx:whoosh], [sfx:pop], [sfx:ding], [sfx:cash_register], 
[sfx:sparkle], [sfx:record_scratch], [sfx:swoosh_up], [sfx:swoosh_down],
[sfx:notification], [sfx:timer_tick], [sfx:click], [sfx:drumroll], 
[sfx:applause], [sfx:camera_shutter], [sfx:bass_drop], [sfx:coin], [sfx:success]

Rules:
- Max 2-3 SFX per 15-second block
- Place [sfx:] BEFORE the word it accompanies
- NEVER stack two SFX back-to-back
- Common: hook=[sfx:record_scratch], reveal=[sfx:sparkle], 
  price=[sfx:cash_register], CTA=[sfx:click], transition=[sfx:whoosh]

EXAMPLE:
"[sfx:record_scratch] Okay wait. (excited) This $7 cream [sfx:sparkle] 
just beat a $200 brand in every single test. [pause] 
I'm not even kidding. [sfx:cash_register] And right now it's 40% off."
"""
```

Also append the prosody instructions from the knowledge base (Section 3).

### 2.2 — SFX extraction before TTS

```python
import re

def extract_sfx_markers(script_text: str) -> tuple[str, list[dict]]:
    """Strip [sfx:NAME] from text, record positions for later mixing."""
    sfx_markers = []
    pattern = r'\[sfx:(\w+)\]'
    
    for match in re.finditer(pattern, script_text):
        text_before = script_text[:match.start()]
        word_count = len(re.sub(pattern, '', text_before).split())
        sfx_markers.append({"name": match.group(1), "word_position": word_count})
    
    clean_text = re.sub(pattern, '', script_text).strip()
    clean_text = re.sub(r'\s+', ' ', clean_text)
    return clean_text, sfx_markers


def resolve_sfx_timing(sfx_markers: list, whisperx_words: list) -> list[dict]:
    """After TTS + WhisperX, resolve SFX to exact timestamps."""
    resolved = []
    for marker in sfx_markers:
        wp = marker["word_position"]
        if wp < len(whisperx_words):
            timestamp = whisperx_words[wp]["start"]
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

### 2.3 — SFX mixing in FFmpeg render

During final composition, overlay SFX at correct timestamps:

```python
for i, sfx in enumerate(resolved_sfx):
    delay_ms = int(sfx["timestamp"] * 1000)
    sfx_filters.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.7[sfx{i}]")

# Mix: voice + ducked_music + all SFX
mix_inputs = "[voice][music_ducked]" + "".join(f"[sfx{i}]" for i in range(len(resolved_sfx)))
final_filter = f"{mix_inputs}amix=inputs={2 + len(resolved_sfx)}:duration=first[final_audio]"
```

### 2.4 — Source SFX clips

Download CC0 clips from Freesound.org, normalize to -3dB, convert to MP3 44.1kHz, upload to `s3://luminacast/sfx/`:

```bash
# Search queries for each SFX on freesound.org:
# whoosh → "swoosh transition short"
# pop → "bubble pop cartoon"
# ding → "notification bell short"
# cash_register → "cash register ka-ching"
# sparkle → "magic sparkle shine"
# record_scratch → "vinyl record scratch"
# swoosh_up → "swoosh rise ascending"
# swoosh_down → "swoosh fall descending"
# notification → "phone notification ding"
# timer_tick → "clock tick single"
# click → "button click UI"
# drumroll → "drumroll short"
# applause → "applause cheering short"
# camera_shutter → "camera shutter click"
# bass_drop → "bass drop impact"
# typing → "keyboard typing"
# coin → "coin drop"
# success → "success chime"

# All clips: CC0 license, under 2 seconds, normalized, MP3
```

---

## PART 3 — Park Old ACE-Step Code

### 3.1 — Feature flag

Do NOT delete any ACE-Step code. Wrap it:

```tsx
// In the Music page:
const ACE_STEP_ENABLED = false;  // flip when ACE-Step is reinstalled on HOSTKEY

{ACE_STEP_ENABLED ? (
  <AceStepMusicGenerator />
) : (
  <div className="text-center py-8 border border-dashed border-white/10 rounded-xl">
    <Construction className="w-8 h-8 mx-auto mb-2 text-amber-400/50" />
    <p className="text-sm text-white/30">AI Music Studio</p>
    <p className="text-[10px] text-white/15 mt-1">
      Full AI music composition with custom instruments and vocals.
      Coming soon.
    </p>
  </div>
)}
```

### 3.2 — Keep ACE-Step backend code

Don't remove any ACE-Step routes, services, or models. Just ensure they're not called from the new Music page. They stay in the codebase for future use.

---

## PART 4 — Music Tab Frontend

**File:** `frontend/companion-app/src/pages/Music.tsx`

### 4.1 — Page layout with 4 tabs

```tsx
const MusicPage = () => {
  const [tab, setTab] = useState<"browse" | "generate" | "sfx" | "uploaded">("browse");
  
  return (
    <div>
      <h1 className="text-xl font-semibold mb-6">Music</h1>
      
      <div className="flex gap-1 bg-white/5 p-1 rounded-lg w-fit mb-6">
        <TabBtn active={tab === "browse"} onClick={() => setTab("browse")}
          icon={<Music />} label="Browse" />
        <TabBtn active={tab === "generate"} onClick={() => setTab("generate")}
          icon={<Sparkles />} label="AI Generate" />
        <TabBtn active={tab === "sfx"} onClick={() => setTab("sfx")}
          icon={<Volume2 />} label="SFX" />
        <TabBtn active={tab === "uploaded"} onClick={() => setTab("uploaded")}
          icon={<Upload />} label="Uploaded" />
      </div>
      
      {tab === "browse" && <BrowseTab />}
      {tab === "generate" && <GenerateTab />}
      {tab === "sfx" && <SFXTab />}
      {tab === "uploaded" && <UploadedTab />}
      
      {/* Parked ACE-Step section at the bottom */}
      <div className="mt-12">
        {ACE_STEP_ENABLED ? <AceStepMusicGenerator /> : <AceStepComingSoon />}
      </div>
    </div>
  );
};
```

### 4.2 — Browse tab (pre-generated catalog)

```tsx
const BrowseTab = () => (
  <div>
    {/* Filter bar */}
    <div className="flex gap-3 mb-4">
      <select className="text-xs bg-white/5 border border-white/10 rounded-lg px-3 py-1.5">
        <option value="all">All moods</option>
        <option value="excited">Energetic</option>
        <option value="calm">Chill</option>
        <option value="confident">Corporate</option>
        <option value="mysterious">Cinematic</option>
        {/* Load from knowledge base Section 1 */}
      </select>
      <select className="text-xs bg-white/5 border border-white/10 rounded-lg px-3 py-1.5">
        <option value="all">All durations</option>
        <option value="30">30s</option>
        <option value="60">60s</option>
        <option value="90">90s</option>
      </select>
    </div>
    
    {/* Track list */}
    {catalogTracks.map(track => (
      <div key={track.id} className="p-3 bg-white/[0.03] border border-white/[0.07] 
        rounded-xl mb-2 hover:bg-white/[0.05] transition-colors">
        <div className="flex items-center gap-3">
          <button onClick={() => togglePlay(track)} 
            className="w-9 h-9 rounded-full bg-accent/20 flex items-center justify-center 
              hover:bg-accent/30 transition-colors flex-shrink-0">
            {playing === track.id ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>
          
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium">{track.name}</div>
            <div className="text-[10px] text-white/30 mt-0.5">
              {track.mood} · {track.duration}s · {track.tags.join(", ")}
            </div>
          </div>
          
          {/* Waveform / progress */}
          <div className="w-32 h-6 bg-white/5 rounded overflow-hidden flex-shrink-0">
            <div className="h-full bg-accent/30 transition-all" 
              style={{ width: `${playProgress[track.id] || 0}%` }} />
          </div>
          
          <span className="text-[10px] text-white/20 w-10 text-right flex-shrink-0">
            {formatDuration(track.duration)}
          </span>
          
          <Button size="sm" variant="outline" onClick={() => addToCast(track)}>
            + Add
          </Button>
        </div>
      </div>
    ))}
  </div>
);
```

### 4.3 — AI Generate tab (Mubert on-demand)

```tsx
const GenerateTab = () => (
  <div>
    <div className="p-4 bg-white/[0.03] border border-white/10 rounded-xl">
      <label className="text-xs text-white/40 mb-2 block">Describe the vibe:</label>
      <input
        value={prompt}
        onChange={e => setPrompt(e.target.value)}
        placeholder="upbeat pop music for a TikTok product showcase"
        className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm mb-3"
      />
      
      <div className="flex gap-3 mb-4">
        <div>
          <label className="text-[10px] text-white/30">Duration</label>
          <select value={duration} onChange={e => setDuration(e.target.value)}
            className="block text-xs bg-white/5 border border-white/10 rounded px-2 py-1 mt-1">
            <option value="auto">Match cast</option>
            <option value="30">30s</option>
            <option value="60">60s</option>
            <option value="90">90s</option>
            <option value="120">2 min</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] text-white/30">Mood</label>
          <select value={mood} onChange={e => setMood(e.target.value)}
            className="block text-xs bg-white/5 border border-white/10 rounded px-2 py-1 mt-1">
            <option value="auto">Auto-detect</option>
            <option value="excited">Energetic</option>
            <option value="calm">Chill</option>
            <option value="confident">Corporate</option>
            <option value="urgent">Intense</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] text-white/30">Intensity</label>
          <select value={intensity} onChange={e => setIntensity(e.target.value)}
            className="block text-xs bg-white/5 border border-white/10 rounded px-2 py-1 mt-1">
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>
      
      <Button onClick={handleGenerate} disabled={generating}>
        {generating ? (
          <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Generating...</>
        ) : (
          <><Sparkles className="w-4 h-4 mr-2" /> Generate 3 Options</>
        )}
      </Button>
    </div>
    
    {/* Generated results */}
    {generatedTracks.length > 0 && (
      <div className="mt-4 space-y-2">
        {generatedTracks.map((track, i) => (
          <TrackCard key={i} track={track} onAdd={addToCast} />
        ))}
      </div>
    )}
  </div>
);
```

### 4.4 — SFX tab (browsable + draggable)

```tsx
const SFXTab = () => {
  // Load SFX library from knowledge base Section 2
  const SFX_ITEMS = [
    { key: "whoosh", icon: "💨", label: "Whoosh" },
    { key: "pop", icon: "🫧", label: "Pop" },
    { key: "ding", icon: "🔔", label: "Ding" },
    { key: "cash_register", icon: "💰", label: "Cash Register" },
    { key: "sparkle", icon: "✨", label: "Sparkle" },
    { key: "record_scratch", icon: "🔴", label: "Record Scratch" },
    { key: "swoosh_up", icon: "📈", label: "Swoosh Up" },
    { key: "swoosh_down", icon: "📉", label: "Swoosh Down" },
    { key: "notification", icon: "📳", label: "Notification" },
    { key: "timer_tick", icon: "⏱", label: "Timer Tick" },
    { key: "click", icon: "🖱", label: "Click" },
    { key: "drumroll", icon: "🥁", label: "Drumroll" },
    { key: "applause", icon: "👏", label: "Applause" },
    { key: "camera_shutter", icon: "📸", label: "Shutter" },
    { key: "bass_drop", icon: "🔊", label: "Bass Drop" },
    { key: "coin", icon: "🪙", label: "Coin" },
    { key: "success", icon: "✅", label: "Success" },
    { key: "typing", icon: "⌨️", label: "Typing" },
  ];
  
  return (
    <div>
      <p className="text-xs text-white/30 mb-4">
        Click to preview · Drag to timeline · SFX are also auto-inserted by AI during script generation
      </p>
      
      <div className="grid grid-cols-6 gap-2">
        {SFX_ITEMS.map(sfx => (
          <button
            key={sfx.key}
            draggable
            onDragStart={e => e.dataTransfer.setData("sfx", sfx.key)}
            onClick={() => previewSFX(sfx.key)}
            className="flex flex-col items-center gap-1.5 p-3 bg-white/[0.03] 
              border border-white/[0.07] rounded-xl hover:bg-white/[0.07] 
              hover:border-white/[0.15] transition-all cursor-grab active:cursor-grabbing"
          >
            <span className="text-2xl">{sfx.icon}</span>
            <span className="text-[10px] text-white/40">{sfx.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
};
```

### 4.5 — Uploaded tab

```tsx
const UploadedTab = () => (
  <div>
    <div className="flex justify-end mb-4">
      <Button size="sm" variant="outline" onClick={handleUpload}>
        <Upload className="w-3.5 h-3.5 mr-1.5" /> Upload Music
      </Button>
    </div>
    
    {uploadedTracks.length > 0 ? (
      uploadedTracks.map(track => <TrackCard key={track.id} track={track} onAdd={addToCast} />)
    ) : (
      <div className="text-center py-12 text-white/20">
        <Music className="w-10 h-10 mx-auto mb-3 opacity-20" />
        <p className="text-sm">No uploaded music yet</p>
        <p className="text-[10px] mt-1">Upload MP3 or WAV files</p>
      </div>
    )}
  </div>
);
```

---

## PART 5 — API Endpoints

```python
# backend/orchestrator/routers/music.py

@router.get("/music/catalog")
async def list_music_catalog(mood: str = None, duration: int = None):
    """Browse pre-generated music catalog."""

@router.post("/music/generate")
async def generate_music(req: GenerateMusicRequest):
    """Generate custom track via Mubert. Returns {tracks: [...]}."""

@router.post("/music/upload")
async def upload_music(file: UploadFile):
    """Upload custom music file."""

@router.get("/sfx/library")
async def list_sfx():
    """List available SFX with preview URLs."""

@router.post("/music/catalog/refresh")
async def refresh_catalog():
    """Admin: trigger batch generation of catalog tracks."""
```

---

## PART 6 — Catalog Batch Generation

Weekly Celery task to pre-generate browsable tracks:

```python
@celery_app.task(queue="default")
def refresh_music_catalog():
    """Generate catalog tracks across all mood presets.
    Presets defined in Music_SFX_Knowledge_Base_v1.md Section 5."""
    
    PRESETS = [
        ("Energetic Pop", "excited", "high", "fast"),
        ("Chill Lo-fi", "calm", "low", "slow"),
        ("Corporate Motivational", "confident", "medium", "medium"),
        ("Dramatic Cinematic", "mysterious", "high", "medium"),
        ("Upbeat Electronic", "hype", "high", "fast"),
        ("Warm Acoustic", "trustworthy", "low", "slow"),
        ("Intense Driving", "urgent", "high", "fast"),
        ("Ambient Minimal", "informative", "low", "slow"),
        ("Fun Quirky", "playful", "medium", "fast"),
        ("Dreamy Ethereal", "dreamy", "low", "slow"),
    ]
    
    mubert = MubertService()
    for name, mood, intensity, tempo in PRESETS:
        for duration in [30, 60, 90]:
            track = asyncio.run(mubert.generate_track(duration, mood, intensity, tempo))
            # Download, upload to R2, store in MusicCatalogTrack model
            
# Celery beat: run weekly
# "refresh-music-catalog": {"task": "...", "schedule": 604800}
```

---

## PART 7 — Live Stream Music

For live streams, Mubert generates long background tracks:

```python
async def generate_live_music(duration_hours: int = 2, mood: str = "enthusiastic"):
    """Generate music for a live stream (in 30-min chunks)."""
    mubert = MubertService()
    chunks = []
    for _ in range(duration_hours * 2):
        track = await mubert.generate_track(1800, mood, "low", "medium")
        chunks.append(track["download_url"])
    return chunks
```

Live SFX triggered by the orchestrator in real-time (on purchase → cash_register, on transition → whoosh).

---

## Implementation order

1. Mubert service wrapper + config (1 hour)
2. SFX prompt additions to `ai_prompts.py` + prosody prompt (30 min)
3. SFX extraction + timing resolution code (1 hour)
4. Source and upload 18 SFX clips from Freesound CC0 (1 hour)
5. Auto-generate music after cast creation (1 hour)
6. Place music + SFX on timeline in editorStarterMapping (1 hour)
7. Audio ducking + SFX mixing in FFmpeg render (2 hours)
8. Music page frontend — 4 tabs (3 hours)
9. API endpoints for catalog, generate, upload, SFX (1 hour)
10. Catalog batch generation task (1 hour)
11. Park ACE-Step behind feature flag (15 min)
12. Live stream music integration (1 hour)

---

## Do NOT touch

- ACE-Step code (park, don't delete)
- Fish Speech TTS (SFX markers stripped before sending)
- InfiniteTalk rendering (music mixed in FFmpeg, not video gen)
- Existing render pipeline structure
- Go Live architecture (live music layers on top)
