import { useState, useEffect, useCallback, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Search,
  Image,
  Film,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Play,
  RefreshCw,
} from "lucide-react";
import { stockMediaApi } from "@/lib/api";

export interface StockMediaItem {
  id: number;
  type: "photo" | "video";
  src: string;
  thumb: string;
  width: number;
  height: number;
  duration?: number;
  photographer: string;
  photographer_url: string;
  pexels_url: string;
}

interface StockMediaPickerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (media: StockMediaItem) => void;
  mediaType?: "photo" | "video" | "both";
  orientation?: "landscape" | "portrait" | "square";
  maxDuration?: number;
}

export function StockMediaPicker({
  open,
  onClose,
  onSelect,
  mediaType = "both",
  orientation,
  maxDuration,
}: StockMediaPickerProps) {
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"photo" | "video">(
    mediaType === "video" ? "video" : "photo"
  );
  const [results, setResults] = useState<StockMediaItem[]>([]);
  const [totalResults, setTotalResults] = useState(0);
  const [page, setPage] = useState(1);
  const [perPage] = useState(20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orientationFilter, setOrientationFilter] = useState<string>(
    orientation || ""
  );
  const [durationFilter, setDurationFilter] = useState<string>("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const doSearch = useCallback(
    async (q: string, p: number, t: "photo" | "video") => {
      if (!q.trim()) {
        setResults([]);
        setTotalResults(0);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        let data: any;
        if (t === "photo") {
          data = await stockMediaApi.searchPhotos({
            q: q.trim(),
            orientation: orientationFilter || undefined,
            page: p,
            per_page: perPage,
          });
        } else {
          const durMap: Record<string, { min?: number; max?: number }> = {
            "5-15": { min: 5, max: 15 },
            "15-30": { min: 15, max: 30 },
            "30-60": { min: 30, max: 60 },
          };
          const dur = durMap[durationFilter] || {};
          data = await stockMediaApi.searchVideos({
            q: q.trim(),
            orientation: orientationFilter || undefined,
            min_duration: dur.min,
            max_duration: maxDuration || dur.max,
            page: p,
          });
        }
        setResults(
          (data.results || []).map((r: any) => ({
            id: r.id,
            type: r.type || t,
            src: r.src,
            thumb: r.thumb,
            width: r.width,
            height: r.height,
            duration: r.duration,
            photographer: r.photographer || r.videographer || "",
            photographer_url: r.photographer_url || r.videographer_url || "",
            pexels_url: r.pexels_url || "",
          }))
        );
        setTotalResults(data.total_results || 0);
      } catch {
        setError("Couldn't load stock media. Try again.");
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [orientationFilter, durationFilter, perPage, maxDuration]
  );

  // Debounced search on query change
  useEffect(() => {
    if (!open) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      doSearch(query, 1, tab);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, tab, orientationFilter, durationFilter, open, doSearch]);

  // Page change
  useEffect(() => {
    if (!open || !query.trim() || page === 1) return;
    doSearch(query, page, tab);
  }, [page]);

  // Reset state on close
  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
      setTotalResults(0);
      setPage(1);
      setError(null);
    }
  }, [open]);

  const totalPages = Math.ceil(totalResults / perPage);

  const handleSelect = (item: StockMediaItem) => {
    onSelect(item);
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-zinc-900 border-white/10 max-w-3xl max-h-[85vh] flex flex-col overflow-hidden p-0">
        <DialogHeader className="px-5 pt-5 pb-0">
          <DialogTitle className="text-white">Stock Media</DialogTitle>
        </DialogHeader>

        {/* Search */}
        <div className="px-5 pt-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search stock photos and videos..."
              className="pl-10 bg-white/5 border-white/10 text-white placeholder:text-white/30"
              data-testid="stock-media-search"
            />
          </div>
        </div>

        {/* Tabs + Filters */}
        <div className="px-5 pt-2 flex items-center gap-3 flex-wrap">
          {/* Media type tabs */}
          {mediaType === "both" && (
            <div className="flex gap-1 bg-white/5 rounded-lg p-0.5">
              <button
                onClick={() => setTab("photo")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  tab === "photo"
                    ? "bg-purple-600 text-white"
                    : "text-white/50 hover:text-white/70"
                }`}
              >
                <Image className="w-3.5 h-3.5" /> Photos
              </button>
              <button
                onClick={() => setTab("video")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  tab === "video"
                    ? "bg-purple-600 text-white"
                    : "text-white/50 hover:text-white/70"
                }`}
              >
                <Film className="w-3.5 h-3.5" /> Videos
              </button>
            </div>
          )}

          {/* Orientation filter */}
          <select
            value={orientationFilter}
            onChange={(e) => setOrientationFilter(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-white/70"
          >
            <option value="">All orientations</option>
            <option value="landscape">Landscape</option>
            <option value="portrait">Portrait</option>
            <option value="square">Square</option>
          </select>

          {/* Duration filter (video only) */}
          {tab === "video" && (
            <select
              value={durationFilter}
              onChange={(e) => setDurationFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-white/70"
            >
              <option value="">Any duration</option>
              <option value="5-15">5-15s</option>
              <option value="15-30">15-30s</option>
              <option value="30-60">30-60s</option>
            </select>
          )}
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto px-5 py-3 min-h-0">
          {/* Loading skeleton */}
          {loading && (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i}>
                  <Skeleton className="w-full aspect-4/3 rounded-lg bg-white/10" />
                  <Skeleton className="h-3 w-2/3 mt-2 bg-white/10" />
                </div>
              ))}
            </div>
          )}

          {/* Error state */}
          {!loading && error && (
            <div className="flex flex-col items-center justify-center py-12 text-white/50">
              <p className="text-sm mb-3">{error}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => doSearch(query, page, tab)}
                className="border-white/10"
              >
                <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Retry
              </Button>
            </div>
          )}

          {/* Empty state */}
          {!loading && !error && query.trim() && results.length === 0 && (
            <div className="text-center py-12 text-white/40 text-sm">
              No results for "{query}". Try a different search term.
            </div>
          )}

          {/* Prompt to search */}
          {!loading && !error && !query.trim() && (
            <div className="text-center py-12 text-white/30 text-sm">
              Search for stock photos and videos above
            </div>
          )}

          {/* Results grid */}
          {!loading && !error && results.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {results.map((item) => (
                <button
                  key={`${item.type}-${item.id}`}
                  onClick={() => handleSelect(item)}
                  className="group text-left rounded-lg overflow-hidden border border-white/5 hover:border-purple-500/50 transition-colors cursor-pointer"
                  data-testid={`stock-result-${item.id}`}
                >
                  <div className="relative aspect-4/3 bg-black/30 overflow-hidden">
                    <img
                      src={item.thumb}
                      alt={item.photographer}
                      loading="lazy"
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                    />
                    {item.type === "video" && (
                      <>
                        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                          <div className="w-10 h-10 rounded-full bg-black/60 flex items-center justify-center">
                            <Play className="w-5 h-5 text-white fill-white" />
                          </div>
                        </div>
                        {item.duration && (
                          <span className="absolute bottom-1.5 right-1.5 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded">
                            {Math.floor(item.duration / 60)}:
                            {String(item.duration % 60).padStart(2, "0")}
                          </span>
                        )}
                      </>
                    )}
                  </div>
                  <div className="px-2 py-1.5">
                    <p className="text-[11px] text-white/40 truncate">
                      {item.type === "photo" ? "Photo" : "Video"} by{" "}
                      <a
                        href={item.photographer_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-white/60 hover:text-purple-400 hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {item.photographer}
                      </a>
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Pagination */}
        {!loading && totalPages > 1 && (
          <div className="px-5 py-2 flex items-center justify-center gap-4 border-t border-white/5">
            <Button
              size="sm"
              variant="ghost"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="text-white/60"
            >
              <ChevronLeft className="w-4 h-4 mr-1" /> Prev
            </Button>
            <span className="text-xs text-white/40">
              Page {page} of {totalPages}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="text-white/60"
            >
              Next <ChevronRight className="w-4 h-4 ml-1" />
            </Button>
          </div>
        )}

        {/* Attribution footer */}
        <div className="border-t border-white/10 px-4 py-2 text-center">
          <span className="text-xs text-white/40">
            Photos and videos provided by{" "}
            <a
              href="https://www.pexels.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-purple-400 hover:underline"
            >
              Pexels
            </a>
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
