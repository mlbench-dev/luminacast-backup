import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { musicApi, type LibraryTrack } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Loader2, Play, Pause, Search } from "lucide-react";
import { useToast } from "@/hooks/useToast";

/**
 * MusicTrackPickerModal — browse the real ~12K-track Mubert-backed library
 * (the same one the standalone Music page uses) and pick one for a cast's
 * background music.
 *
 * Setup's "Specific track" mode used to offer only 5 hardcoded placeholder
 * tracks (services/music_library.py) with no connection to the actual
 * library or its AI-generated content. This replaces that with a real
 * search-and-preview picker over the same GET /music/library/tracks +
 * /music/library/params endpoints the Music page's Browse tab uses.
 *
 * Deliberately a lighter, self-contained picker rather than reusing the
 * Music page's BrowseTab/TrackCard directly — those are private to that
 * page and built for a full multi-tab music workspace, not a single-purpose
 * pick-one-and-close modal.
 */

const PAGE_SIZE = 24;

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
        toast({ title: "Couldn't play track", description: "The audio file couldn't be played.", variant: "destructive" });
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
    a.play().then(() => setPlayingUrl(url)).catch(() => setPlayingUrl(null));
  }, [playingUrl, toast]);

  useEffect(() => () => {
    audioRef.current?.pause();
    audioRef.current = null;
  }, []);

  return { playingUrl, toggle };
}

export function MusicTrackPickerModal({
  open,
  onClose,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (track: LibraryTrack) => void;
}) {
  const [mood, setMood] = useState<string>("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [pages, setPages] = useState<LibraryTrack[][]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const { playingUrl, toggle } = useAudioPreview();

  const { data: params } = useQuery({
    queryKey: ["music-library-params"],
    queryFn: () => musicApi.libraryParams(),
    enabled: open,
    staleTime: 5 * 60_000,
  });

  const loadPage = useCallback(async (nextOffset: number, reset: boolean) => {
    setLoading(true);
    try {
      const res = await musicApi.libraryTracks({
        mood: mood || undefined,
        offset: nextOffset,
        limit: PAGE_SIZE,
      });
      setPages((prev) => (reset ? [res.tracks] : [...prev, res.tracks]));
      setOffset(nextOffset + res.tracks.length);
      setHasMore(res.has_more);
    } catch {
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }, [mood]);

  // Reset and load the first page whenever the modal opens or the mood
  // filter changes.
  useEffect(() => {
    if (!open) return;
    setPages([]);
    setOffset(0);
    setHasMore(true);
    loadPage(0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mood]);

  const allTracks = useMemo(() => pages.flat(), [pages]);
  const visibleTracks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allTracks;
    return allTracks.filter((t) => t.name.toLowerCase().includes(q) || t.description?.toLowerCase().includes(q));
  }, [allTracks, search]);

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Choose background music</DialogTitle>
        </DialogHeader>

        <div className="flex items-center gap-2 shrink-0">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/30" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter loaded tracks by name…"
              className="pl-8"
            />
          </div>
          <select
            value={mood}
            onChange={(e) => setMood(e.target.value)}
            className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white/85 focus:outline-none focus:border-accent/40"
          >
            <option value="">All moods</option>
            {(params?.moods || []).map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
        </div>

        <div className="flex-1 overflow-y-auto -mx-1 px-1 space-y-1.5 mt-2">
          {loading && pages.length === 0 && (
            <div className="text-center py-10 text-white/30 text-sm">
              <Loader2 className="w-5 h-5 mx-auto animate-spin mb-2" /> Loading…
            </div>
          )}
          {!loading && visibleTracks.length === 0 && (
            <p className="text-center py-10 text-sm text-white/40">No tracks match.</p>
          )}
          {visibleTracks.map((t) => (
            <div
              key={t.id}
              className="flex items-center gap-3 rounded-lg border border-white/[0.07] bg-white/[0.03] hover:bg-white/[0.06] px-3 py-2.5 transition-colors"
            >
              <button
                type="button"
                onClick={() => toggle(t.url)}
                className="shrink-0 w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center"
                title={playingUrl === t.url ? "Pause" : "Preview"}
              >
                {playingUrl === t.url ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 ml-0.5" />}
              </button>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-white truncate">{t.name}</div>
                <div className="text-[11px] text-white/40 truncate">{t.description || t.mood}</div>
              </div>
              <button
                type="button"
                onClick={() => onSelect(t)}
                className="shrink-0 rounded-md bg-accent/15 hover:bg-accent/25 text-accent px-3 py-1.5 text-xs font-medium transition-colors"
              >
                Use this
              </button>
            </div>
          ))}
          {hasMore && !search && visibleTracks.length > 0 && (
            <button
              type="button"
              onClick={() => loadPage(offset, false)}
              disabled={loading}
              className="w-full text-center py-2 text-xs text-white/50 hover:text-white/80 disabled:opacity-50"
            >
              {loading ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
