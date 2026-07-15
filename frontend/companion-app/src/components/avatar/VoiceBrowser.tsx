import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, Pause, Loader2, Filter, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { avatarApi } from "@/lib/api";
import { cn } from "@/lib/cn";

interface VoiceBrowserProps {
  avatarId: string;
  onSelectVoice: (voiceId: string, voiceName: string) => void;
  onClose: () => void;
}

const GENDER_FILTERS = [
  { value: "all", label: "All" },
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
];

const TONE_TAGS = ["Warm", "Professional", "Energetic", "Calm", "Authoritative", "Friendly", "Dramatic"];

export function VoiceBrowser({ avatarId, onSelectVoice, onClose }: VoiceBrowserProps) {
  const [gender, setGender] = useState("all");
  const [search, setSearch] = useState("");
  const [selectedTones, setSelectedTones] = useState<string[]>([]);
  const [filtersApplied, setFiltersApplied] = useState(false);
  const [page, setPage] = useState(1);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["voice-library", gender, search, page],
    queryFn: () => avatarApi.aiGetVoices({ gender: gender !== "all" ? gender : undefined, search: search || undefined, page }),
    enabled: filtersApplied,
  });

  const voices = data?.voices || [];
  const total = data?.total || 0;

  const toggleTone = (tone: string) => {
    setSelectedTones((prev) =>
      prev.includes(tone) ? prev.filter((t) => t !== tone) : [...prev, tone]
    );
  };

  const applyFilters = () => {
    setFiltersApplied(true);
    setPage(1);
  };

  const playVoice = useCallback((voiceId: string, sampleUrl: string) => {
    if (playingId === voiceId) {
      audioEl?.pause();
      setPlayingId(null);
      return;
    }
    audioEl?.pause();
    const audio = new Audio(sampleUrl);
    audio.onended = () => setPlayingId(null);
    audio.play();
    setAudioEl(audio);
    setPlayingId(voiceId);
  }, [playingId, audioEl]);

  return (
    <div className="space-y-4" data-testid="voice-browser">
      {/* Filter panel */}
      <div className="rounded-lg border border-border bg-surface p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-text-muted" />
          <span className="text-xs font-medium text-text">Filter voices</span>
        </div>

        {/* Gender */}
        <div>
          <label className="mb-1.5 block text-[10px] font-medium text-text-muted uppercase tracking-wide">Gender</label>
          <div className="flex gap-1.5">
            {GENDER_FILTERS.map((g) => (
              <button
                key={g.value}
                onClick={() => setGender(g.value)}
                className={cn(
                  "rounded-full px-3 py-1 text-xs font-medium transition",
                  gender === g.value ? "bg-accent text-white" : "bg-bg border border-border text-text-muted hover:border-accent/40",
                )}
                data-testid={`voice-filter-gender-${g.value}`}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>

        {/* Tone tags */}
        <div>
          <label className="mb-1.5 block text-[10px] font-medium text-text-muted uppercase tracking-wide">Tone</label>
          <div className="flex flex-wrap gap-1.5">
            {TONE_TAGS.map((tone) => (
              <button
                key={tone}
                onClick={() => toggleTone(tone)}
                className={cn(
                  "rounded-full px-3 py-1 text-xs font-medium transition",
                  selectedTones.includes(tone)
                    ? "bg-accent text-white"
                    : "bg-bg border border-border text-text-muted hover:border-accent/40",
                )}
                data-testid={`voice-filter-tone-${tone.toLowerCase()}`}
              >
                {tone}
              </button>
            ))}
          </div>
        </div>

        {/* Search */}
        <input
          type="text"
          placeholder="Search by name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-xs text-text focus:border-accent focus:outline-hidden"
        />

        <Button size="sm" onClick={applyFilters} className="w-full" data-testid="apply-voice-filters">
          <Filter className="mr-1.5 h-3.5 w-3.5" /> Browse voices
        </Button>
      </div>

      {/* Results */}
      {!filtersApplied ? (
        <div className="rounded-lg border border-dashed border-border py-8 text-center">
          <p className="text-sm text-text-muted" data-testid="voice-filter-empty-msg">Pick filters above to browse voices</p>
        </div>
      ) : isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
        </div>
      ) : voices.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border py-8 text-center">
          <p className="text-sm text-text-muted">No voices found. Try different filters.</p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {voices.map((voice: any) => (
              <div
                key={voice.voice_id}
                className="rounded-lg border border-border bg-surface p-3 space-y-2"
                data-testid={`voice-card-${voice.voice_id}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-text truncate">{voice.name}</span>
                  <div className="flex gap-1">
                    {voice.gender && voice.gender !== "unknown" && (
                      <Badge className="text-[9px]">{voice.gender}</Badge>
                    )}
                  </div>
                </div>
                {voice.description && (
                  <p className="text-[10px] text-text-muted line-clamp-2">{voice.description}</p>
                )}
                <div className="flex items-center gap-2">
                  {voice.sample_url && (
                    <button
                      onClick={() => playVoice(voice.voice_id, voice.sample_url)}
                      className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] text-text-muted hover:border-accent/40 transition"
                    >
                      {playingId === voice.voice_id ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
                      Preview
                    </button>
                  )}
                  <Button
                    size="sm"
                    className="text-[10px] h-6 px-2 ml-auto"
                    onClick={() => onSelectVoice(voice.voice_id, voice.name)}
                    data-testid="confirm-voice-btn"
                  >
                    <Check className="mr-1 h-3 w-3" /> Select
                  </Button>
                </div>
              </div>
            ))}
          </div>

          {/* Pagination */}
          {total > voices.length && (
            <div className="flex justify-center">
              <Button size="sm" variant="outline" onClick={() => setPage((p) => p + 1)} disabled={isLoading}>
                Load more voices
              </Button>
            </div>
          )}
        </>
      )}

      <div className="flex justify-center">
        <Button size="sm" variant="outline" onClick={onClose}>
          Close voice browser
        </Button>
      </div>
    </div>
  );
}
