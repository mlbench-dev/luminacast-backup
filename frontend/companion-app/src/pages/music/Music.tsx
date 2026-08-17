import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  aceStepApi,
  musicApi,
  castsApi,
  type MusicSoundCast,
  type MusicTrackItem,
  type UploadedTrack,
  type SfxItem,
  type AIGeneratedTrack,
  type LibraryTrack,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import {
  Plus,
  Music,
  Music2,
  Upload,
  Trash2,
  Download,
  Loader2,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  Sparkles,
  Volume2,
  Play,
  Pause,
  Construction,
  Search,
  X,
  Bookmark,
} from "lucide-react";

// Reusable compact search box used across all music tabs.
//
// - Visible search icon button on the left (clickable; fires onSubmit).
// - Pressing Enter fires onSubmit(value).
// - Pressing Escape clears the value.
// - As the user types, onChange fires so callers can do client-side filtering.
function SearchBox({
  value,
  onChange,
  onSubmit,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: (v: string) => void;
  placeholder?: string;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  return (
    <div className="relative flex items-center min-w-[200px] flex-1 max-w-[320px]">
      <button
        type="button"
        onClick={() => {
          onSubmit?.(value);
          // Visible feedback that the click did something — flash the
          // input by re-focusing.
          inputRef.current?.focus();
        }}
        className="absolute left-1.5 p-1 rounded-md text-white/50 hover:text-white hover:bg-white/10 transition"
        aria-label="Search"
      >
        <Search className="w-3.5 h-3.5" />
      </button>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            // Filtering already runs as the user types; Enter calls the
            // optional onSubmit hook (used by tabs that need to refetch
            // server-side, e.g. Browse) and blurs to give visible feedback.
            onSubmit?.(value);
            (e.target as HTMLInputElement).blur();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onChange("");
          }
        }}
        placeholder={placeholder}
        className="w-full text-xs bg-white/5 border border-white/10 rounded-lg pl-9 pr-7 py-1.5 text-white placeholder:text-white/30 focus:outline-none focus:border-white/25"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          className="absolute right-2 text-white/40 hover:text-white"
          aria-label="Clear search"
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

// Feature flag — flip to true once ACE-Step is reinstalled on HOSTKEY.
// All ACE-Step code is preserved below in <AceStepMusicGenerator/> but it
// only mounts when this is true.
const ACE_STEP_ENABLED = false;

type SoundCast = MusicSoundCast;
type MusicTrack = MusicTrackItem;

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function formatDuration(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(seconds || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}:${r.toString().padStart(2, "0")}` : `${r}s`;
}

function getCastIdFromPath(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/cst_[A-Za-z0-9_-]+/);
  return m ? m[0] : null;
}

async function attachToCast(
  castId: string,
  url: string,
  mood?: string | null,
): Promise<void> {
  await castsApi.attachMusic(castId, url, mood ?? null);
}

// ─────────────────────────────────────────────────────────────────────────
// Top-level Music page
// ─────────────────────────────────────────────────────────────────────────

type MainTab = "browse" | "generate" | "sfx" | "uploaded" | "saved";

export function MusicPage() {
  const [tab, setTab] = useState<MainTab>("browse");
  const castId = getCastIdFromPath();

  return (
    <div className="max-w-5xl mx-auto px-6 py-6 space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Music</h1>
          <p className="text-xs text-white/40 mt-0.5">
            Royalty-free AI music for your casts. Browse presets, generate
            custom tracks, layer in SFX, or upload your own.
          </p>
        </div>
      </header>

      <div className="flex gap-1 bg-white/5 p-1 rounded-xl w-fit">
        <TabButton
          active={tab === "browse"}
          onClick={() => setTab("browse")}
          icon={<Music className="w-3.5 h-3.5" />}
          label="Browse"
        />
        <TabButton
          active={tab === "generate"}
          onClick={() => setTab("generate")}
          icon={<Sparkles className="w-3.5 h-3.5" />}
          label="AI Generate"
        />
        <TabButton
          active={tab === "sfx"}
          onClick={() => setTab("sfx")}
          icon={<Volume2 className="w-3.5 h-3.5" />}
          label="SFX"
        />
        <TabButton
          active={tab === "uploaded"}
          onClick={() => setTab("uploaded")}
          icon={<Upload className="w-3.5 h-3.5" />}
          label="Uploaded"
        />
        <TabButton
          active={tab === "saved"}
          onClick={() => setTab("saved")}
          icon={<Bookmark className="w-3.5 h-3.5" />}
          label="Saved"
        />
      </div>

      <div className="border-t border-white/[0.06] pt-5">
        {tab === "browse" && <BrowseTab castId={castId} />}
        {tab === "generate" && <GenerateTab castId={castId} />}
        {tab === "sfx" && <SFXTab />}
        {tab === "uploaded" && <UploadedTab castId={castId} />}
        {tab === "saved" && <SavedTab castId={castId} />}
      </div>

      {/* Parked AI Music Studio (ACE-Step) section */}
      <div className="mt-12 border-t border-white/[0.06] pt-6">
        <h2 className="text-sm font-semibold text-white/70 mb-3">
          AI Music Studio
        </h2>
        {ACE_STEP_ENABLED ? (
          <AceStepMusicGenerator />
        ) : (
          <div className="text-center py-10 border border-dashed border-white/10 rounded-xl">
            <Construction className="w-8 h-8 mx-auto mb-2 text-amber-400/50" />
            <p className="text-sm text-white/40">AI Music Studio (coming soon)</p>
            <p className="text-[11px] text-white/25 mt-1 max-w-md mx-auto">
              Full AI music composition with custom instruments, vocals, and
              your own trained Sound Casts. Coming soon.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition",
        active
          ? "bg-white/10 text-white"
          : "text-white/50 hover:text-white/80 hover:bg-white/5",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Audio preview (single shared <audio> element across cards)
// ─────────────────────────────────────────────────────────────────────────

// HTMLMediaElement's own "error" event (via a.error.code) is the
// authoritative source for *why* playback failed — a rejected play()
// promise alone doesn't distinguish "blocked by autoplay policy" from
// "404" from "not actually audio". MediaError codes: 1 aborted,
// 2 network, 3 decode (corrupt/truncated file), 4 src unsupported
// (missing, wrong content-type, or a genuinely bad format).
const MEDIA_ERROR_MESSAGES: Record<number, string> = {
  1: "Playback was interrupted.",
  2: "A network error prevented the track from loading.",
  3: "This file looks corrupted or truncated — try generating it again.",
  4: "This audio source isn't available (missing file or unsupported format).",
};

function useAudioPreview() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingUrl, setPlayingUrl] = useState<string | null>(null);
  const { toast } = useToast();

  const toggle = useCallback((url: string) => {
    if (!audioRef.current) {
      const el = new Audio();
      el.addEventListener("ended", () => setPlayingUrl(null));
      el.addEventListener("pause", () => {
        if (audioRef.current && audioRef.current.paused) setPlayingUrl(null);
      });
      el.addEventListener("error", () => {
        setPlayingUrl(null);
        const code = el.error?.code;
        toast({
          title: "Couldn't play track",
          description: (code && MEDIA_ERROR_MESSAGES[code]) || "The audio file couldn't be played.",
          variant: "destructive",
        });
      });
      audioRef.current = el;
    }
    const a = audioRef.current;
    if (playingUrl === url) {
      a.pause();
      setPlayingUrl(null);
      return;
    }
    a.src = url;
    a.play()
      .then(() => setPlayingUrl(url))
      .catch((err: unknown) => {
        setPlayingUrl(null);
        // The element's own "error" listener above already fires (and
        // toasts) for load/decode failures — only toast here for
        // rejections that don't, like the browser blocking autoplay.
        if (err instanceof DOMException && err.name === "NotAllowedError") {
          toast({
            title: "Couldn't play track",
            description: "Your browser blocked playback — click play again.",
            variant: "destructive",
          });
        }
      });
  }, [playingUrl, toast]);

  useEffect(() => () => {
    audioRef.current?.pause();
    audioRef.current = null;
  }, []);

  return { playingUrl, toggle };
}

// ─────────────────────────────────────────────────────────────────────────
// Track card (used by Browse / Generate / Uploaded)
// ─────────────────────────────────────────────────────────────────────────

function TrackCard({
  title,
  subtitle,
  url,
  duration,
  playingUrl,
  onTogglePlay,
  onAdd,
  onDelete,
  onSave,
  saved,
  saving,
}: {
  title: string;
  subtitle?: string;
  url: string;
  duration?: number | null;
  playingUrl: string | null;
  onTogglePlay: (url: string) => void;
  onAdd?: () => void;
  onDelete?: () => void;
  onSave?: () => void;
  saved?: boolean;
  saving?: boolean;
}) {
  const isPlaying = playingUrl === url;
  return (
    <div className="p-3 bg-white/[0.03] border border-white/[0.07] rounded-xl hover:bg-white/[0.05] transition-colors">
      <div className="flex items-center gap-3">
        <button
          onClick={() => onTogglePlay(url)}
          className="w-9 h-9 rounded-full bg-primary/20 flex items-center justify-center hover:bg-primary/30 transition-colors flex-shrink-0"
        >
          {isPlaying ? (
            <Pause className="w-4 h-4" />
          ) : (
            <Play className="w-4 h-4 ml-0.5" />
          )}
        </button>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-white truncate">{title}</div>
          {subtitle && (
            <div className="text-[10px] text-white/30 mt-0.5 truncate">
              {subtitle}
            </div>
          )}
        </div>

        {duration != null && (
          <span className="text-[10px] text-white/30 w-12 text-right flex-shrink-0">
            {formatDuration(duration)}
          </span>
        )}

        {onSave && (
          <Button
            size="sm"
            variant="outline"
            onClick={onSave}
            disabled={saved || saving}
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
            ) : (
              <Bookmark className={cn("w-3.5 h-3.5 mr-1", saved && "fill-current")} />
            )}
            {saved ? "Saved" : "Save"}
          </Button>
        )}

        {onAdd && (
          <Button size="sm" variant="outline" onClick={onAdd}>
            <Plus className="w-3.5 h-3.5 mr-1" /> Add
          </Button>
        )}

        {onDelete && (
          <button
            onClick={onDelete}
            className="text-white/30 hover:text-red-400 transition-colors p-1"
            title="Delete"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Browse tab — pre-generated catalog
// ─────────────────────────────────────────────────────────────────────────

const ALL_VALUE = "__all__";
const PAGE_SIZE = 20;

// Try to map an arbitrary search query to one of the available enum
// values returned by /music/library/params. We do best-effort case-
// insensitive substring matching: "cinema" matches "Cinematic", "60s"
// matches a duration option labelled "60 seconds", etc. The user still
// gets free-text client-side filtering via the search input itself; this
// just lets Enter "drive" the dropdowns when the typed text is unambiguous.
function bestMatch(
  query: string,
  options: Array<{ label: string; value: string | number }>,
): string | undefined {
  const q = query.trim().toLowerCase();
  if (!q) return undefined;
  const exact = options.find(
    (o) => String(o.label).toLowerCase() === q || String(o.value).toLowerCase() === q,
  );
  if (exact) return String(exact.value);
  const partial = options.find(
    (o) =>
      String(o.label).toLowerCase().includes(q) ||
      String(o.value).toLowerCase().includes(q),
  );
  return partial ? String(partial.value) : undefined;
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  allLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<{ label: string; value: string | number }>;
  allLabel: string;
}) {
  return (
    <div className="flex flex-col gap-1 min-w-[140px]">
      <span className="text-[10px] uppercase tracking-wide text-white/35">
        {label}
      </span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-8">
          <SelectValue placeholder={allLabel} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_VALUE}>{allLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={String(o.value)} value={String(o.value)}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function BrowseTab({ castId }: { castId: string | null }) {
  const { toast } = useToast();
  const [genre, setGenre] = useState<string>(ALL_VALUE);
  const [mood, setMood] = useState<string>(ALL_VALUE);
  const [bpm, setBpm] = useState<string>(ALL_VALUE);
  const [duration, setDuration] = useState<string>(ALL_VALUE);
  const [search, setSearch] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState("");
  const { playingUrl, toggle } = useAudioPreview();

  const { data: params } = useQuery({
    queryKey: ["music-library-params"],
    queryFn: () => musicApi.libraryParams(),
    staleTime: 12 * 60 * 60 * 1000,
  });

  const filterArgs = useMemo(
    () => ({
      genre: genre === ALL_VALUE ? undefined : genre,
      mood: mood === ALL_VALUE ? undefined : mood,
      bpm: bpm === ALL_VALUE ? undefined : bpm,
      duration:
        duration === ALL_VALUE ? undefined : Number(duration) || undefined,
    }),
    [genre, mood, bpm, duration],
  );

  const [pages, setPages] = useState<LibraryTrack[][]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Reset paging when filters change.
  useEffect(() => {
    setPages([]);
    setOffset(0);
    setHasMore(true);
  }, [filterArgs.genre, filterArgs.mood, filterArgs.bpm, filterArgs.duration, reloadKey]);

  const loadMore = useCallback(async () => {
    if (loading || !hasMore) return;
    setLoading(true);
    try {
      const res = await musicApi.libraryTracks({
        ...filterArgs,
        offset,
        limit: PAGE_SIZE,
      });
      setPages((p) => [...p, res.tracks]);
      setOffset((o) => o + (res.tracks.length || PAGE_SIZE));
      setHasMore(Boolean(res.has_more) && res.tracks.length > 0);
    } catch {
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }, [loading, hasMore, filterArgs, offset]);

  // Initial load + reload-on-filter-change.
  useEffect(() => {
    if (pages.length === 0 && hasMore && !loading) {
      void loadMore();
    }
  }, [pages.length, hasMore, loading, loadMore]);

  // IntersectionObserver-based infinite scroll.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void loadMore();
      },
      { rootMargin: "120px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [loadMore]);

  const allTracks = useMemo(() => pages.flat(), [pages]);
  const filteredTracks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allTracks;
    return allTracks.filter((t) =>
      [t.name, t.mood, t.genre, t.intensity, t.tempo, t.prompt, ...(t.moods || []), ...(t.genres || [])]
        .filter(Boolean)
        .some((s) => String(s).toLowerCase().includes(q)),
    );
  }, [allTracks, search]);

  const handleAdd = async (track: LibraryTrack) => {
    if (!castId) {
      toast({
        title: "Open a cast first",
        description: "Open a cast in the builder, then add music from here.",
      });
      return;
    }
    try {
      await attachToCast(castId, track.url, track.mood);
      toast({ title: "Music added to cast", description: track.name });
    } catch (e: any) {
      toast({
        title: "Couldn't add music",
        description: e?.message || "",
        variant: "destructive",
      });
    }
  };

  // When the user hits Enter on the search box, try to coerce their query
  // into the dropdown filters (best-effort substring match against the
  // params enums). This lets typing "cinematic" + Enter set mood=Cinematic.
  const onSubmitSearch = useCallback(
    (q: string) => {
      setSubmittedSearch(q);
      if (!params || !q.trim()) return;
      const matchedGenre = bestMatch(q, params.genres);
      if (matchedGenre) setGenre(matchedGenre);
      const matchedMood = bestMatch(q, params.moods);
      if (matchedMood) setMood(matchedMood);
      const matchedBpm = bestMatch(q, params.bpms);
      if (matchedBpm) setBpm(matchedBpm);
      const matchedDur = bestMatch(q, params.durations);
      if (matchedDur) setDuration(matchedDur);
    },
    [params],
  );
  void submittedSearch;

  return (
    <div className="space-y-4">
      <p className="text-xs text-white/45 leading-relaxed">
        Search 12,000+ royalty-free tracks. Filter by genre, mood, BPM, or
        duration. Click <span className="text-white/70">+ Add</span> to drop a
        track into your cast.
      </p>

      <div className="flex gap-3 items-end flex-wrap">
        <div className="flex-1 min-w-[200px] max-w-[320px]">
          <span className="block text-[10px] uppercase tracking-wide text-white/35 mb-1">
            Search
          </span>
          <SearchBox
            value={search}
            onChange={setSearch}
            onSubmit={onSubmitSearch}
            placeholder="Try cinematic, lofi, 120 BPM…"
          />
        </div>
        <FilterSelect
          label="Genre"
          value={genre}
          onChange={setGenre}
          options={params?.genres || []}
          allLabel="All genres"
        />
        <FilterSelect
          label="Mood"
          value={mood}
          onChange={setMood}
          options={params?.moods || []}
          allLabel="All moods"
        />
        <FilterSelect
          label="BPM"
          value={bpm}
          onChange={setBpm}
          options={params?.bpms || []}
          allLabel="All BPM"
        />
        <FilterSelect
          label="Duration"
          value={duration}
          onChange={setDuration}
          options={params?.durations || []}
          allLabel="All durations"
        />
        <button
          onClick={() => setReloadKey((k) => k + 1)}
          className="h-8 px-2 text-white/40 hover:text-white rounded-lg hover:bg-white/5 transition self-end"
          title="Refresh"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      {pages.length === 0 && loading && (
        <div className="text-center py-12 text-white/30 text-sm">
          <Loader2 className="w-5 h-5 mx-auto animate-spin mb-2" />
          Loading library…
        </div>
      )}

      {pages.length > 0 && filteredTracks.length === 0 && (
        <div className="text-center py-12 border border-dashed border-white/10 rounded-xl">
          <Music className="w-10 h-10 mx-auto mb-3 text-white/15" />
          <p className="text-sm text-white/40">No matches</p>
          <p className="text-[11px] text-white/25 mt-1 max-w-md mx-auto">
            Try a broader filter, clear your search, or hit{" "}
            <span className="text-white/50">AI Generate</span> to make
            something custom.
          </p>
        </div>
      )}

      {filteredTracks.length > 0 && (
        <div className="space-y-2">
          {filteredTracks.map((t) => (
            <TrackCard
              key={t.id || t.url}
              // Title: theme/genre name (e.g. "Cinematic"). Description:
              // pre-formatted "120 BPM · C#m · high · mix" from backend so
              // we don't repeat info between title and subtitle.
              title={t.name}
              subtitle={t.description || t.prompt || ""}
              url={t.url}
              duration={t.duration}
              playingUrl={playingUrl}
              onTogglePlay={toggle}
              onAdd={() => handleAdd(t)}
            />
          ))}
        </div>
      )}

      <div ref={sentinelRef} className="h-4" />

      {hasMore && pages.length > 0 && (
        <div className="text-center py-4 text-white/30 text-xs flex items-center justify-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading more…
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// AI Generate tab
// ─────────────────────────────────────────────────────────────────────────

const GENERATE_DURATION_OPTIONS = [15, 30, 60, 90, 120, 180];
const GENERATE_MOOD_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "enthusiastic", label: "Enthusiastic" },
  { value: "excited", label: "Energetic" },
  { value: "calm", label: "Chill" },
  { value: "confident", label: "Corporate" },
  { value: "urgent", label: "Intense" },
  { value: "mysterious", label: "Cinematic" },
  { value: "hype", label: "Hype" },
  { value: "trustworthy", label: "Warm" },
  { value: "informative", label: "Ambient" },
  { value: "playful", label: "Playful" },
  { value: "dreamy", label: "Dreamy" },
  { value: "intimate", label: "Intimate" },
  { value: "emotional", label: "Emotional" },
  { value: "triumphant", label: "Triumphant" },
];

function GenerateTab({ castId }: { castId: string | null }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  // Mutually exclusive by design: the backend only ever uses the typed
  // description OR the mood preset, never both (a typed prompt silently
  // overrides mood entirely). This toggle makes that explicit in the UI
  // instead of showing both inputs as if they combine.
  const [inputMode, setInputMode] = useState<"mood" | "custom">("mood");
  const [prompt, setPrompt] = useState("");
  const [mood, setMood] = useState("enthusiastic");
  const [duration, setDuration] = useState(60);
  const [intensity, setIntensity] = useState("medium");
  const [generated, setGenerated] = useState<AIGeneratedTrack[]>([]);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const { playingUrl, toggle } = useAudioPreview();

  const generateMutation = useMutation({
    mutationFn: () =>
      musicApi.generate({
        prompt: inputMode === "custom" ? prompt.trim() || undefined : undefined,
        mood: inputMode === "mood" ? mood : undefined,
        duration_seconds: duration,
        intensity,
        cast_id: castId || undefined,
      }),
    onSuccess: (track) => {
      setGenerated((prev) => [track, ...prev]);
      toast({ title: "Track generated", description: inputMode === "custom" ? prompt : mood });
    },
    onError: (e: any) =>
      toast({
        title: "Generation failed",
        description: e?.response?.data?.detail || e?.message || "",
        variant: "destructive",
      }),
  });

  const saveMutation = useMutation({
    mutationFn: (track: AIGeneratedTrack) => musicApi.generatedSave(track),
    onSuccess: (_res, track) => {
      setSavedIds((prev) => new Set(prev).add(track.id));
      queryClient.invalidateQueries({ queryKey: ["ai-generated-music"] });
      toast({ title: "Saved" });
    },
    onError: (e: any) =>
      toast({
        title: "Couldn't save track",
        description: e?.response?.data?.detail || e?.message || "",
        variant: "destructive",
      }),
  });

  const handleAdd = async (track: AIGeneratedTrack) => {
    if (!castId) {
      toast({
        title: "Open a cast first",
        description: "Open a cast in the builder, then add music from here.",
      });
      return;
    }
    try {
      await attachToCast(castId, track.url, track.mood ?? mood);
      toast({ title: "Music added to cast" });
    } catch (e: any) {
      toast({
        title: "Couldn't add music",
        description: e?.message || "",
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-white/45 leading-relaxed">
        Generate a custom royalty-free track in seconds. Describe the vibe in
        your own words or pick a mood preset, then choose a duration and
        intensity.
      </p>

      <div className="p-4 bg-white/[0.03] border border-white/10 rounded-xl space-y-4">
        <div className="flex gap-1 bg-white/5 p-1 rounded-lg w-fit">
          <button
            type="button"
            onClick={() => setInputMode("mood")}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
              inputMode === "mood" ? "bg-white/10 text-white" : "text-white/40 hover:text-white/70",
            )}
          >
            Mood preset
          </button>
          <button
            type="button"
            onClick={() => setInputMode("custom")}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
              inputMode === "custom" ? "bg-white/10 text-white" : "text-white/40 hover:text-white/70",
            )}
          >
            Describe your own vibe
          </button>
        </div>

        {inputMode === "mood" ? (
          <div>
            <label className="text-[11px] text-white/40 mb-1 block">Mood</label>
            <Select value={mood} onValueChange={setMood}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {GENERATE_MOOD_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <div>
            <label className="text-[11px] text-white/40 mb-1.5 block">
              Describe the vibe
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value.slice(0, 300))}
              placeholder="upbeat pop music for a TikTok product showcase"
              rows={2}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder:text-white/20 focus:outline-hidden focus:ring-1 focus:ring-primary"
            />
            <span className="text-[10px] text-white/25">{prompt.length}/300</span>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-white/40 mb-1 block">
              Duration
            </label>
            <Select
              value={String(duration)}
              onValueChange={(v) => setDuration(Number(v))}
            >
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {GENERATE_DURATION_OPTIONS.map((d) => (
                  <SelectItem key={d} value={String(d)}>
                    {d}s
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[11px] text-white/40 mb-1 block">
              Intensity
            </label>
            <Select value={intensity} onValueChange={setIntensity}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">Low</SelectItem>
                <SelectItem value="medium">Medium</SelectItem>
                <SelectItem value="high">High</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <Button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="w-full"
        >
          {generateMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Generating… (this can take 5–30s)
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4 mr-2" />
              Generate Track
            </>
          )}
        </Button>
      </div>

      {generated.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-white/60">Recent generations</h3>
          {generated.map((t) => (
            <TrackCard
              key={t.id}
              title={t.prompt || t.mood || "AI track"}
              subtitle={`${t.mood ?? mood} · ${
                t.intensity ?? intensity
              }${t.bpm ? ` · ${t.bpm} BPM` : ""}`}
              url={t.url}
              duration={t.duration}
              playingUrl={playingUrl}
              onTogglePlay={toggle}
              onSave={() => saveMutation.mutate(t)}
              saved={savedIds.has(t.id)}
              saving={saveMutation.isPending && saveMutation.variables?.id === t.id}
              onAdd={() => handleAdd(t)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// SFX tab
// ─────────────────────────────────────────────────────────────────────────

const SFX_PAGE_SIZE = 30;

function SFXTab() {
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["sfx-library"],
    queryFn: () => musicApi.sfxLibrary(),
  });
  const itemsRaw: SfxItem[] = data?.items ?? [];
  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return itemsRaw;
    return itemsRaw.filter((sfx) =>
      [sfx.label, sfx.description, sfx.key]
        .filter(Boolean)
        .some((s) => String(s).toLowerCase().includes(q)),
    );
  }, [itemsRaw, search]);

  // Lazy-load chunks of SFX_PAGE_SIZE so very large libraries don't render
  // hundreds of nodes at once. Reset when the search changes.
  const [visibleCount, setVisibleCount] = useState(SFX_PAGE_SIZE);
  useEffect(() => {
    setVisibleCount(SFX_PAGE_SIZE);
  }, [search, itemsRaw.length]);

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisibleCount((n) => Math.min(items.length, n + SFX_PAGE_SIZE));
        }
      },
      { rootMargin: "120px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [items.length]);

  const visible = items.slice(0, visibleCount);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const preview = (sfx: SfxItem) => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }
    audioRef.current.src = sfx.url;
    audioRef.current.play().catch((e) => {
      toast({
        title: "Preview failed",
        description: e?.message || "",
        variant: "destructive",
      });
    });
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-white/45 leading-relaxed">
        Punctuate moments with sound. Click any clip to preview, then drag it
        to the timeline. SFX are also auto-inserted by AI during script
        generation.
      </p>

      <div className="flex items-center gap-3 flex-wrap">
        <SearchBox
          value={search}
          onChange={setSearch}
          onSubmit={setSearch}
          placeholder="Search SFX (whoosh, ding, applause…)"
        />
        {/* Live result count — visible feedback that filtering is working,
            so pressing Enter or just typing isn't a black hole. */}
        {search && (
          <span className="text-[11px] text-white/40">
            {items.length} of {itemsRaw.length} matched
          </span>
        )}
      </div>

      {isLoading && (
        <div className="text-center py-8 text-white/30 text-sm">
          <Loader2 className="w-5 h-5 mx-auto animate-spin mb-2" />
          Loading…
        </div>
      )}

      {!isLoading && items.length === 0 && (
        <div className="text-center py-10 border border-dashed border-white/10 rounded-xl text-white/40 text-sm">
          {search ? "No SFX matched your search" : "No SFX library yet"}
        </div>
      )}

      {visible.length > 0 && (
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
          {visible.map((sfx) => (
            <button
              key={sfx.key}
              draggable
              onDragStart={(e) =>
                e.dataTransfer.setData(
                  "application/x-luminacast-sfx",
                  JSON.stringify({
                    key: sfx.key,
                    url: sfx.url,
                    duration: sfx.duration,
                  }),
                )
              }
              onClick={() => preview(sfx)}
              title={sfx.description}
              className="flex flex-col items-center gap-1.5 p-3 bg-white/[0.03] border border-white/[0.07] rounded-xl hover:bg-white/[0.07] hover:border-white/[0.15] transition-all cursor-grab active:cursor-grabbing"
            >
              <span className="text-2xl leading-none">{sfx.icon}</span>
              <span className="text-[10px] text-white/50 text-center">
                {sfx.label}
              </span>
              <span className="text-[9px] text-white/25">
                {sfx.duration.toFixed(1)}s
              </span>
            </button>
          ))}
        </div>
      )}

      <div ref={sentinelRef} className="h-4" />

      {visibleCount < items.length && (
        <div className="text-center py-3 text-white/30 text-xs flex items-center justify-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading more…
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Uploaded tab
// ─────────────────────────────────────────────────────────────────────────

function UploadedTab({ castId }: { castId: string | null }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const { playingUrl, toggle } = useAudioPreview();

  const { data, isLoading } = useQuery({
    queryKey: ["uploaded-music"],
    queryFn: () => musicApi.uploadedList(),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => musicApi.uploadedUpload(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["uploaded-music"] });
      toast({ title: "Uploaded" });
    },
    onError: (e: any) =>
      toast({
        title: "Upload failed",
        description: e?.response?.data?.detail || e?.message || "",
        variant: "destructive",
      }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => musicApi.uploadedDelete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["uploaded-music"] });
      toast({ title: "Deleted" });
    },
    onError: (e: any) =>
      toast({
        title: "Delete failed",
        description: e?.message || "",
        variant: "destructive",
      }),
  });

  const handleAdd = async (t: UploadedTrack) => {
    if (!castId) {
      toast({
        title: "Open a cast first",
        description: "Open a cast in the builder, then add music from here.",
      });
      return;
    }
    try {
      await attachToCast(castId, t.url, null);
      toast({ title: "Music added to cast", description: t.name });
    } catch (e: any) {
      toast({
        title: "Couldn't add music",
        description: e?.message || "",
        variant: "destructive",
      });
    }
  };

  const tracksRaw = data?.tracks ?? [];
  const tracks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tracksRaw;
    return tracksRaw.filter((t) =>
      [t.name].filter(Boolean).some((s) => String(s).toLowerCase().includes(q)),
    );
  }, [tracksRaw, search]);

  return (
    <div className="space-y-4">
      <p className="text-xs text-white/45 leading-relaxed">
        Bring your own music. Drop in MP3 or WAV files (up to 25 MB) and add
        them to any cast.
      </p>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <SearchBox
          value={search}
          onChange={setSearch}
          onSubmit={setSearch}
          placeholder="Search uploads…"
        />
        <input
          ref={fileRef}
          type="file"
          accept=".mp3,.wav,audio/mpeg,audio/wav,audio/x-wav"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadMutation.mutate(f);
            if (fileRef.current) fileRef.current.value = "";
          }}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => fileRef.current?.click()}
          disabled={uploadMutation.isPending}
        >
          {uploadMutation.isPending ? (
            <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
          ) : (
            <Upload className="w-3.5 h-3.5 mr-1.5" />
          )}
          Upload Music
        </Button>
      </div>

      {isLoading && (
        <div className="text-center py-8 text-white/30 text-sm">
          <Loader2 className="w-5 h-5 mx-auto animate-spin mb-2" />
          Loading…
        </div>
      )}

      {!isLoading && tracks.length === 0 && (
        <div className="text-center py-12 border border-dashed border-white/10 rounded-xl">
          <Music className="w-10 h-10 mx-auto mb-3 text-white/15" />
          <p className="text-sm text-white/40">No uploaded music yet</p>
          <p className="text-[11px] text-white/25 mt-1">
            Upload MP3 or WAV files (max 25 MB)
          </p>
        </div>
      )}

      {!isLoading && tracks.length > 0 && (
        <div className="space-y-2">
          {tracks.map((t) => (
            <TrackCard
              key={t.id}
              title={t.name}
              subtitle={
                t.file_size_bytes
                  ? `${(t.file_size_bytes / 1024 / 1024).toFixed(1)} MB`
                  : undefined
              }
              url={t.url}
              duration={t.duration}
              playingUrl={playingUrl}
              onTogglePlay={toggle}
              onAdd={() => handleAdd(t)}
              onDelete={() => deleteMutation.mutate(t.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Saved tab — persisted history of AI Generate results
// ─────────────────────────────────────────────────────────────────────────

function SavedTab({ castId }: { castId: string | null }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const { playingUrl, toggle } = useAudioPreview();

  const { data, isLoading } = useQuery({
    queryKey: ["ai-generated-music"],
    queryFn: () => musicApi.generatedList(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => musicApi.generatedDelete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ai-generated-music"] });
      toast({ title: "Deleted" });
    },
    onError: (e: any) =>
      toast({
        title: "Delete failed",
        description: e?.response?.data?.detail || e?.message || "",
        variant: "destructive",
      }),
  });

  const handleAdd = async (t: AIGeneratedTrack) => {
    if (!castId) {
      toast({
        title: "Open a cast first",
        description: "Open a cast in the builder, then add music from here.",
      });
      return;
    }
    try {
      await attachToCast(castId, t.url, t.mood ?? null);
      toast({ title: "Music added to cast", description: t.name || t.prompt });
    } catch (e: any) {
      toast({
        title: "Couldn't add music",
        description: e?.message || "",
        variant: "destructive",
      });
    }
  };

  const tracksRaw = data?.tracks ?? [];
  const tracks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tracksRaw;
    return tracksRaw.filter((t) =>
      [t.name, t.prompt, t.mood, t.intensity].filter(Boolean).some((s) => String(s).toLowerCase().includes(q)),
    );
  }, [tracksRaw, search]);

  return (
    <div className="space-y-4">
      <p className="text-xs text-white/45 leading-relaxed">
        Every track you've generated in AI Generate, saved automatically so
        it's still here after you leave the page.
      </p>

      <SearchBox
        value={search}
        onChange={setSearch}
        onSubmit={setSearch}
        placeholder="Search saved tracks…"
      />

      {isLoading && (
        <div className="text-center py-8 text-white/30 text-sm">
          <Loader2 className="w-5 h-5 mx-auto animate-spin mb-2" />
          Loading…
        </div>
      )}

      {!isLoading && tracks.length === 0 && (
        <div className="text-center py-12 border border-dashed border-white/10 rounded-xl">
          <Bookmark className="w-10 h-10 mx-auto mb-3 text-white/15" />
          <p className="text-sm text-white/40">No saved tracks yet</p>
          <p className="text-[11px] text-white/25 mt-1">
            Tracks you generate in <span className="text-white/50">AI Generate</span> show up here automatically.
          </p>
        </div>
      )}

      {!isLoading && tracks.length > 0 && (
        <div className="space-y-2">
          {tracks.map((t) => (
            <TrackCard
              key={t.id}
              title={t.name || t.prompt || "AI track"}
              subtitle={`${t.mood ?? ""}${t.intensity ? ` · ${t.intensity}` : ""}${t.bpm ? ` · ${t.bpm} BPM` : ""}`}
              url={t.url}
              duration={t.duration}
              playingUrl={playingUrl}
              onTogglePlay={toggle}
              onAdd={() => handleAdd(t)}
              onDelete={() => deleteMutation.mutate(t.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════
// AI Music Studio (parked) — original ACE-Step Sound Cast UI.
// All this code is preserved verbatim and only mounts when
// ACE_STEP_ENABLED === true.
// ═════════════════════════════════════════════════════════════════════════

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  training_pending: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  training_in_progress: "bg-blue-500/20 text-blue-400 border-blue-500/30 animate-pulse",
  trained: "bg-green-500/20 text-green-400 border-green-500/30",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
  pending: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  generating: "bg-blue-500/20 text-blue-400 border-blue-500/30 animate-pulse",
  ready: "bg-green-500/20 text-green-400 border-green-500/30",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  training_pending: "Pending",
  training_in_progress: "Training...",
  trained: "Trained",
  failed: "Failed",
  pending: "Pending",
  generating: "Generating",
  ready: "Ready",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("text-[10px] font-medium border", STATUS_COLORS[status] || "bg-gray-500/20 text-gray-400")}
    >
      {STATUS_LABELS[status] || status.replace(/_/g, " ")}
    </Badge>
  );
}

function TrainingTab({ sc, onRefresh }: { sc: SoundCast; onRefresh: () => void }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadPrompt, setUploadPrompt] = useState("");

  const uploadMutation = useMutation({
    mutationFn: (file: File) => aceStepApi.uploadTrainingAudio(sc.id, file, uploadPrompt),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sound-casts"] });
      queryClient.invalidateQueries({ queryKey: ["sound-cast", sc.id] });
      setUploadPrompt("");
      onRefresh();
    },
    onError: (e: Error) => toast({ title: "Upload failed", description: e.message, variant: "destructive" }),
  });

  const trainMutation = useMutation({
    mutationFn: () => aceStepApi.startTraining(sc.id, { steps: sc.training_steps }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sound-casts"] });
      queryClient.invalidateQueries({ queryKey: ["sound-cast", sc.id] });
      toast({ title: "Training started", description: "Usually takes 20–40 minutes on GPU." });
      onRefresh();
    },
    onError: (e: Error) => toast({ title: "Training failed", description: e.message, variant: "destructive" }),
  });

  // (cancelMutation kept for parity — referenced from kebab in older UI)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _cancelMutation = useMutation({
    mutationFn: () => aceStepApi.cancelTraining(sc.id),
    onSuccess: () => {
      toast({ title: "Training cancelled" });
      onRefresh();
    },
    onError: (e: Error) => toast({ title: "Cancel failed", description: e.message, variant: "destructive" }),
  });

  const deleteAudioMutation = useMutation({
    mutationFn: (idx: number) => aceStepApi.deleteTrainingAudio(sc.id, idx),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sound-casts"] });
      queryClient.invalidateQueries({ queryKey: ["sound-cast", sc.id] });
      onRefresh();
    },
    onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }),
  });

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      Array.from(files).forEach((f) => uploadMutation.mutate(f));
    },
    [uploadMutation],
  );

  const canTrain = sc.training_audio_count >= 5 && !["training_in_progress"].includes(sc.status);
  const isTraining = sc.status === "training_in_progress";
  const canUpload = !isTraining && sc.status !== "trained";

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border bg-card/50 p-4">
        <h4 className="mb-2 text-sm font-semibold text-foreground">How it works</h4>
        <ol className="space-y-1 text-sm text-muted-foreground list-decimal list-inside">
          <li>Upload 5{"–"}20 audio clips that represent the style you want</li>
          <li>Add a <strong>unique description per clip</strong></li>
          <li>Hit {"“"}Start Training{"”"} {"—"} takes about 60{"–"}90 min on GPU</li>
          <li>Once trained, go to Generate tab to create tracks</li>
        </ol>
      </div>

      {canUpload && (
        <div>
          <div className="mb-2">
            <Input
              placeholder="Style description for this clip"
              value={uploadPrompt}
              onChange={(e) => setUploadPrompt(e.target.value)}
            />
          </div>
          <div
            className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-border p-8 transition-colors hover:border-primary/50 hover:bg-card/50"
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onDrop={(e) => { e.preventDefault(); e.stopPropagation(); handleFiles(e.dataTransfer.files); }}
          >
            <Upload className="mb-2 h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Drop audio files here or click to browse</p>
            <input
              ref={fileRef}
              type="file"
              accept=".mp3,.wav,audio/mpeg,audio/wav,audio/x-wav"
              multiple
              className="hidden"
              onChange={(e) => handleFiles(e.target.files)}
            />
          </div>
        </div>
      )}

      {sc.training_audio_count > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium text-muted-foreground">
            Training Clips ({sc.training_audio_count})
          </h4>
          <div className="space-y-2">
            {(sc.training_audio_keys || []).map((key, i) => (
              <div key={key} className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
                <Music2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">{key.split("/").pop()}</p>
                  <p className="text-xs text-muted-foreground truncate">
                    {(sc.training_prompts || [])[i] || "No description"}
                  </p>
                </div>
                {canUpload && (
                  <button
                    onClick={() => deleteAudioMutation.mutate(i)}
                    className="shrink-0 text-muted-foreground hover:text-destructive transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {isTraining && (
        <div className="flex items-center gap-3 rounded-lg border border-blue-500/30 bg-blue-500/10 p-4">
          <Loader2 className="h-5 w-5 animate-spin text-blue-400 shrink-0" />
          <div>
            <p className="text-sm font-medium text-blue-400">Training in progress</p>
            <p className="text-xs text-blue-400/70">Usually takes 20&ndash;40 minutes</p>
          </div>
        </div>
      )}

      {sc.status === "trained" && (
        <div className="flex items-center gap-3 rounded-lg border border-green-500/30 bg-green-500/10 p-4">
          <CheckCircle2 className="h-5 w-5 text-green-400 shrink-0" />
          <p className="text-sm font-medium text-green-400">Trained &mdash; ready to generate</p>
        </div>
      )}

      <div className="flex items-center gap-3">
        {canTrain && (
          <Button onClick={() => trainMutation.mutate()} disabled={trainMutation.isPending || sc.training_audio_count < 5}>
            {trainMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : sc.status === "failed" ? (
              <RefreshCw className="mr-2 h-4 w-4" />
            ) : null}
            {sc.status === "failed" ? "Retry Training" : "Start Training"}
          </Button>
        )}
      </div>
    </div>
  );
}

function AceStepGenerateTab({ sc }: { sc: SoundCast }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [duration, setDuration] = useState(60);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [seed, setSeed] = useState(-1);
  const [guidanceScale, setGuidanceScale] = useState(15.0);
  const [inferenceSteps, setInferenceSteps] = useState(60);
  const [scheduler, setScheduler] = useState("euler");

  const generateMutation = useMutation({
    mutationFn: () =>
      aceStepApi.generateTrack(sc.id, {
        name, prompt, lyrics, duration_seconds: duration, seed,
        guidance_scale: guidanceScale, inference_steps: inferenceSteps,
        scheduler_type: scheduler,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tracks", sc.id] });
      toast({ title: "Track generation started" });
      setName(""); setPrompt(""); setLyrics("");
    },
    onError: (e: Error) => toast({ title: "Generation failed", description: e.message, variant: "destructive" }),
  });

  if (sc.status !== "trained") {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <Music2 className="mb-3 h-10 w-10 text-muted-foreground/40" />
        <h3 className="text-sm font-medium text-foreground">Train your Sound Cast first</h3>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">Track Name</label>
        <Input value={name} onChange={(e) => setName(e.target.value.slice(0, 80))} placeholder="My Track" />
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">Style Prompt</label>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value.slice(0, 500))}
          rows={3}
          className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">Lyrics (optional)</label>
        <textarea
          value={lyrics}
          onChange={(e) => setLyrics(e.target.value.slice(0, 2000))}
          rows={4}
          className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label className="mb-2 block text-xs font-medium text-muted-foreground">Duration: {duration}s</label>
        <div className="flex gap-2">
          {[30, 60, 90, 120].map((m) => (
            <button
              key={m}
              onClick={() => setDuration(m)}
              className={cn(
                "flex-1 rounded-md border px-3 py-2 text-sm font-medium",
                duration === m
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border bg-card text-muted-foreground",
              )}
            >
              {m}s
            </button>
          ))}
        </div>
      </div>
      <button onClick={() => setShowAdvanced(!showAdvanced)} className="flex items-center gap-1 text-xs text-muted-foreground">
        {showAdvanced ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />} Advanced Settings
      </button>
      {showAdvanced && (
        <div className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-card/50 p-4">
          <div>
            <label className="mb-1 block text-[11px] text-muted-foreground">Guidance Scale</label>
            <input type="range" min={1} max={30} step={0.5} value={guidanceScale} onChange={(e) => setGuidanceScale(Number(e.target.value))} className="w-full" />
            <span className="text-xs">{guidanceScale}</span>
          </div>
          <div>
            <label className="mb-1 block text-[11px] text-muted-foreground">Inference Steps</label>
            <input type="range" min={20} max={100} step={1} value={inferenceSteps} onChange={(e) => setInferenceSteps(Number(e.target.value))} className="w-full" />
            <span className="text-xs">{inferenceSteps}</span>
          </div>
          <div>
            <label className="mb-1 block text-[11px] text-muted-foreground">Scheduler</label>
            <select value={scheduler} onChange={(e) => setScheduler(e.target.value)} className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm">
              <option value="euler">Euler</option><option value="heun">Heun</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] text-muted-foreground">Seed</label>
            <Input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          </div>
        </div>
      )}
      <Button onClick={() => generateMutation.mutate()} disabled={!name.trim() || !prompt.trim() || generateMutation.isPending} className="w-full" size="lg">
        {generateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />} Generate Track
      </Button>
    </div>
  );
}

function TracksTab({ sc }: { sc: SoundCast }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: tracks } = useQuery({
    queryKey: ["tracks", sc.id],
    queryFn: () => aceStepApi.listTracks(sc.id),
    refetchInterval: (query) => {
      const list = query.state.data || [];
      const hasPending = list.some((t: MusicTrack) => ["pending", "generating"].includes(t.status));
      return hasPending ? 10000 : false;
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (trackId: string) => aceStepApi.deleteTrack(trackId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tracks", sc.id] });
      toast({ title: "Track deleted" });
    },
    onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }),
  });
  if (!tracks || tracks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <Music2 className="mb-3 h-10 w-10 text-muted-foreground/40" />
        <h3 className="text-sm font-medium text-foreground">No tracks yet</h3>
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {tracks.map((t: MusicTrack) => (
        <div key={t.id} className="rounded-lg border border-border bg-card p-4">
          <div className="mb-2 flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium text-foreground truncate">{t.name}</p>
              <p className="text-xs text-muted-foreground truncate mt-0.5">{t.prompt}</p>
            </div>
            <StatusBadge status={t.status} />
          </div>
          {t.status === "ready" && t.audio_url && (
            <audio controls src={t.audio_url} className="mt-2 w-full h-8" preload="none" />
          )}
          {t.status === "failed" && t.generation_error && (
            <div className="mt-2 flex items-start gap-2 text-xs text-red-400">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="break-all">{t.generation_error}</span>
            </div>
          )}
          <div className="mt-3 flex items-center justify-end gap-2">
            {t.audio_url && t.status === "ready" && (
              <a href={t.audio_url} download={`${t.name}.wav`} className="text-muted-foreground hover:text-primary">
                <Download className="h-4 w-4" />
              </a>
            )}
            <button onClick={() => deleteMutation.mutate(t.id)} className="text-muted-foreground hover:text-destructive">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function AceStepMusicGenerator() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"training" | "generate" | "tracks">("training");
  const [newName, setNewName] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const { data: soundCasts, refetch: refetchSoundCasts } = useQuery({
    queryKey: ["sound-casts"],
    queryFn: () => aceStepApi.listSoundCasts(),
    refetchInterval: (query) => {
      const list = query.state.data || [];
      return list.some((s: SoundCast) => s.status === "training_in_progress") ? 5000 : false;
    },
  });

  const { data: selectedSc } = useQuery({
    queryKey: ["sound-cast", selectedId],
    queryFn: () => aceStepApi.getSoundCast(selectedId!),
    enabled: !!selectedId,
  });

  const createMutation = useMutation({
    mutationFn: () => aceStepApi.createSoundCast({ name: newName }),
    onSuccess: (sc: SoundCast) => {
      queryClient.invalidateQueries({ queryKey: ["sound-casts"] });
      setSelectedId(sc.id);
      setActiveTab("training");
      setShowCreate(false);
      setNewName("");
    },
    onError: (e: Error) => toast({ title: "Create failed", description: e.message, variant: "destructive" }),
  });

  const sc = selectedSc || (soundCasts || []).find((s: SoundCast) => s.id === selectedId) || null;

  const handleRefresh = useCallback(() => {
    refetchSoundCasts();
    if (selectedId) queryClient.invalidateQueries({ queryKey: ["sound-cast", selectedId] });
  }, [refetchSoundCasts, selectedId, queryClient]);

  if (!selectedId && soundCasts && soundCasts.length > 0) setSelectedId(soundCasts[0].id);

  if (soundCasts && soundCasts.length === 0 && !showCreate) {
    return (
      <div className="text-center py-10 border border-dashed border-white/10 rounded-xl">
        <Music2 className="w-8 h-8 mx-auto mb-2 text-white/30" />
        <p className="text-sm text-white/50">No Sound Casts yet</p>
        <Button size="sm" className="mt-3" onClick={() => setShowCreate(true)}>
          <Plus className="mr-1 h-3.5 w-3.5" /> New Sound Cast
        </Button>
      </div>
    );
  }

  return (
    <div className="flex border border-border rounded-xl overflow-hidden">
      <div className="w-60 shrink-0 border-r border-border flex flex-col">
        <div className="border-b border-border p-3">
          <Button size="sm" className="w-full" onClick={() => setShowCreate(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" /> New Sound Cast
          </Button>
        </div>
        {showCreate && (
          <div className="border-b border-border p-3 bg-card/50">
            <Input
              autoFocus
              placeholder="Name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newName.trim()) createMutation.mutate();
                if (e.key === "Escape") { setShowCreate(false); setNewName(""); }
              }}
            />
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={() => createMutation.mutate()} disabled={!newName.trim() || createMutation.isPending}>Create</Button>
              <Button size="sm" variant="ghost" onClick={() => { setShowCreate(false); setNewName(""); }}>Cancel</Button>
            </div>
          </div>
        )}
        <div className="flex-1 overflow-y-auto">
          {(soundCasts || []).map((s: SoundCast) => (
            <div
              key={s.id}
              onClick={() => { setSelectedId(s.id); setActiveTab("training"); }}
              className={cn(
                "cursor-pointer border-b border-border px-3 py-3",
                s.id === selectedId ? "bg-primary/5 border-l-2 border-l-primary" : "hover:bg-card/80",
              )}
            >
              <span className="font-medium text-sm text-foreground truncate block">{s.name}</span>
              <StatusBadge status={s.status} />
            </div>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {!sc ? (
          <div className="p-6 text-sm text-muted-foreground">Select a Sound Cast.</div>
        ) : (
          <div className="p-6 max-w-3xl">
            <div className="mb-6">
              <h3 className="text-lg font-bold text-foreground">{sc.name}</h3>
              <StatusBadge status={sc.status} />
            </div>
            <div className="mb-6 flex gap-1 rounded-lg border border-border bg-card p-1">
              {(["training", "generate", "tracks"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveTab(t)}
                  className={cn(
                    "flex-1 rounded-md px-3 py-2 text-sm font-medium",
                    activeTab === t ? "bg-primary/10 text-primary" : "text-muted-foreground",
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
            {activeTab === "training" && <TrainingTab sc={sc} onRefresh={handleRefresh} />}
            {activeTab === "generate" && <AceStepGenerateTab sc={sc} />}
            {activeTab === "tracks" && <TracksTab sc={sc} />}
          </div>
        )}
      </div>
    </div>
  );
}
