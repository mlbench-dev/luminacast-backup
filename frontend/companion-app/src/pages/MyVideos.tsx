import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search, Trash2, Upload } from "lucide-react";
import { Sentry } from "@/lib/sentry";
import {
  api,
  stockMediaApi,
  userVideosApi,
  userPhotosApi,
  generatedVideosApi,
  generatedPhotosApi,
} from "@/lib/api";
import { MediaTile } from "@/components/MediaTile";
import { SmartPagination } from "@/components/SmartPagination";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import type { MediaItem, MediaItemType } from "@/lib/mediaTypes";

/** Stock items live in a third-party catalog and are not owned by the user. */
function isDeletable(item: MediaItem): boolean {
  return item.source === "library" || item.source === "generated";
}

type SourceTab = "all" | "library" | "stock" | "generated";
type TypeFilter = "all" | "video" | "photo";

const STOCK_PER_PAGE = 15;
const GENERATED_LIMIT = 50;

function isPhoto(name?: string, fileType?: string, mediaType?: string): boolean {
  if (mediaType === "photo") return true;
  if (fileType?.startsWith("image/")) return true;
  if (name && /\.(jpg|jpeg|png|webp|gif)$/i.test(name)) return true;
  return false;
}

function libraryVideoToItem(v: any): MediaItem {
  const photo = isPhoto(v.name, v.file_type, v.media_type);
  return {
    id: v.id,
    source: "library",
    type: photo ? "photo" : "video",
    thumbnail: v.thumbnail || v.url,
    url: v.url,
    duration: v.duration_seconds || undefined,
    name: v.name,
    width: v.width,
    height: v.height,
    file_size_bytes: v.file_size_bytes,
  };
}

function libraryPhotoToItem(p: any): MediaItem {
  return {
    id: p.id,
    source: "library",
    type: "photo",
    thumbnail: p.thumbnail || p.url,
    url: p.url,
    name: p.name,
    width: p.width,
    height: p.height,
    file_size_bytes: p.file_size_bytes,
  };
}

function stockToItem(r: any, type: MediaItemType): MediaItem {
  return {
    id: r.id,
    source: "stock",
    type,
    thumbnail: r.thumb,
    src: r.src,
    url: r.src,
    duration: r.duration,
    name: `Pexels ${type} by ${r.photographer || r.videographer || "unknown"}`,
    photographer: r.photographer || r.videographer || "",
    photographer_url: r.photographer_url || r.videographer_url || "",
    pexels_url: r.pexels_url || "",
    width: r.width,
    height: r.height,
  };
}

function generatedVideoToItem(v: any): MediaItem {
  return {
    id: v.id,
    source: "generated",
    type: "video",
    thumbnail: v.thumbnail || v.url,
    url: v.url,
    duration: v.duration,
    name: v.prompt ? v.prompt.slice(0, 60) : "Generated video",
  };
}

function generatedPhotoToItem(p: any): MediaItem {
  return {
    id: p.id,
    source: "generated",
    type: "photo",
    thumbnail: p.url,
    url: p.url,
    name: p.prompt ? p.prompt.slice(0, 60) : "Generated photo",
    width: p.width,
    height: p.height,
  };
}

export function MyVideosPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const [sourceTab, setSourceTab] = useState<SourceTab>("all");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<MediaItem[]>([]);
  const [stockTotalResults, setStockTotalResults] = useState(0);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<MediaItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Debounce the search input
  useEffect(() => {
    const t = setTimeout(() => setSearchQuery(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data: libraryStats } = useQuery({
    queryKey: ["my-library-counts"],
    queryFn: async () => {
      const [videos, photos] = await Promise.all([
        userVideosApi.list().catch(() => ({ videos: [] })),
        userPhotosApi.list().catch(() => ({ photos: [] })),
      ]);
      const v = (videos as any).videos?.length ?? 0;
      const p = (photos as any).photos?.length ?? 0;
      return { count: v + p };
    },
  });

  const fetchPage = async (pageNum: number) => {
    setLoading(true);
    try {
      const fetched: MediaItem[] = [];
      let stockTotal = 0;

      // ── Library ───────────────────────────────────────────────────
      if (sourceTab === "all" || sourceTab === "library") {
        try {
          const [videosResp, photosResp] = await Promise.all([
            userVideosApi.list(),
            userPhotosApi.list(),
          ]);
          const libraryVideos = ((videosResp as any).videos || []).map(libraryVideoToItem);
          const libraryPhotos = ((photosResp as any).photos || []).map(libraryPhotoToItem);
          let lib = [...libraryVideos, ...libraryPhotos];
          if (searchQuery) {
            const q = searchQuery.toLowerCase();
            lib = lib.filter((i) => i.name?.toLowerCase().includes(q));
          }
          fetched.push(...lib);
        } catch (e) {
          Sentry.captureException(e);
        }
      }

      // ── Stock (requires search query) ──────────────────────────────
      if ((sourceTab === "all" || sourceTab === "stock") && searchQuery) {
        const wantVideo = typeFilter === "all" || typeFilter === "video";
        const wantPhoto = typeFilter === "all" || typeFilter === "photo";

        if (wantVideo) {
          try {
            const sv = await stockMediaApi.searchVideos({
              q: searchQuery,
              page: pageNum,
              per_page: STOCK_PER_PAGE,
            });
            fetched.push(...((sv as any).results || []).map((r: any) => stockToItem(r, "video")));
            stockTotal = Math.max(stockTotal, (sv as any).total_results || 0);
          } catch (e) {
            Sentry.captureException(e);
          }
        }
        if (wantPhoto) {
          try {
            const sp = await stockMediaApi.searchPhotos({
              q: searchQuery,
              page: pageNum,
              per_page: STOCK_PER_PAGE,
            });
            fetched.push(...((sp as any).results || []).map((r: any) => stockToItem(r, "photo")));
            stockTotal = Math.max(stockTotal, (sp as any).total_results || 0);
          } catch (e) {
            Sentry.captureException(e);
          }
        }
      }

      // ── AI-generated ───────────────────────────────────────────────
      if (sourceTab === "all" || sourceTab === "generated") {
        try {
          const [gv, gp] = await Promise.all([
            api.get(`/videos/generated?limit=${GENERATED_LIMIT}`).then((r) => r.data).catch(() => ({ videos: [] })),
            api.get(`/photos/generated?limit=${GENERATED_LIMIT}`).then((r) => r.data).catch(() => ({ photos: [] })),
          ]);
          let gen: MediaItem[] = [
            ...((gv as any).videos || []).map(generatedVideoToItem),
            ...((gp as any).photos || []).map(generatedPhotoToItem),
          ];
          if (searchQuery) {
            const q = searchQuery.toLowerCase();
            gen = gen.filter((i) => i.name?.toLowerCase().includes(q));
          }
          fetched.push(...gen);
        } catch (e) {
          Sentry.captureException(e);
        }
      }

      // Apply type filter at the end
      const typeFiltered =
        typeFilter === "all" ? fetched : fetched.filter((i) => i.type === typeFilter);

      setItems(typeFiltered);
      setStockTotalResults(stockTotal);
    } catch (err: any) {
      Sentry.captureException(err);
      toast({ title: "Failed to load media", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  // Reset to page 1 when filters or search change
  useEffect(() => {
    setPage(1);
    fetchPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceTab, typeFilter, searchQuery]);

  // Page changes (only meaningful for stock pagination)
  const handlePageChange = (p: number) => {
    setPage(p);
    fetchPage(p);
  };

  const handleAdd = async (item: MediaItem) => {
    if (item.source === "stock") {
      try {
        await stockMediaApi.importMedia({
          pexels_id: typeof item.id === "string" ? parseInt(item.id, 10) : item.id,
          url: item.src || item.url || "",
          type: item.type,
          name: item.name,
        });
        toast({ title: "Added to library" });
        queryClient.invalidateQueries({ queryKey: ["my-library-counts"] });
        queryClient.invalidateQueries({ queryKey: ["user-videos"] });
        queryClient.invalidateQueries({ queryKey: ["user-photos"] });
        fetchPage(page);
      } catch (e: any) {
        Sentry.captureException(e);
        const msg = e?.response?.data?.detail || e?.message || "Failed to add to library";
        toast({ title: "Failed to add", description: msg, variant: "destructive" });
      }
    } else {
      // Already in library / generated — no-op on this page (no cast context).
      toast({ title: "Already in library" });
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const photo = isPhoto(file.name, file.type);
      if (photo) {
        await userPhotosApi.upload(file);
      } else {
        await userVideosApi.upload(file);
      }
      queryClient.invalidateQueries({ queryKey: ["my-library-counts"] });
      queryClient.invalidateQueries({ queryKey: ["user-videos"] });
      queryClient.invalidateQueries({ queryKey: ["user-photos"] });
      toast({ title: photo ? "Photo uploaded" : "Video uploaded" });
      fetchPage(page);
    } catch (err: any) {
      Sentry.captureException(err);
      const msg = err?.response?.data?.detail || err?.message || "Upload failed";
      toast({ title: "Upload failed", description: msg, variant: "destructive" });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleRequestDelete = (item: MediaItem) => {
    setPendingDelete(item);
  };

  // Optimistic delete: drop the tile immediately, restore on failure. Routes
  // to the same per-source DELETE endpoints used by LuminacastMediaPanel —
  // soft-delete happens server-side, R2 cleanup is best-effort.
  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    const item = pendingDelete;
    const id = String(item.id);
    const previous = items;
    setDeleting(true);
    setItems((prev) => prev.filter((i) => String(i.id) !== id || i.source !== item.source || i.type !== item.type));
    try {
      if (item.source === "library") {
        if (item.type === "video") {
          await userVideosApi.delete(id);
        } else {
          await userPhotosApi.delete(id);
        }
      } else if (item.source === "generated") {
        if (item.type === "video") {
          await generatedVideosApi.delete(id);
        } else {
          await generatedPhotosApi.delete(id);
        }
      } else {
        throw new Error("This item cannot be deleted from here.");
      }
      toast({ title: "Media deleted" });
      queryClient.invalidateQueries({ queryKey: ["my-library-counts"] });
      queryClient.invalidateQueries({ queryKey: ["user-videos"] });
      queryClient.invalidateQueries({ queryKey: ["user-photos"] });
      setPendingDelete(null);
    } catch (err: any) {
      Sentry.captureException(err);
      setItems(previous);
      const description =
        err?.response?.data?.detail || err?.message || "Could not delete this media file.";
      toast({
        title: "Delete failed",
        description: typeof description === "string" ? description : "Could not delete this media file.",
        variant: "destructive",
      });
    } finally {
      setDeleting(false);
    }
  };

  const stockTotalPages = stockTotalResults > 0 ? Math.ceil(stockTotalResults / STOCK_PER_PAGE) : 1;
  const showPagination = sourceTab === "stock" && !!searchQuery && stockTotalPages > 1;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text">Media Library</h1>
          <p className="mt-1 text-sm text-text-dim">
            All your videos, photos, and stock — in one search.
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <input
            ref={fileRef}
            type="file"
            accept=".mp4,.mov,.webm,.m4v,.jpg,.jpeg,.png,.webp,.gif"
            className="hidden"
            onChange={handleUpload}
          />
          <Button onClick={() => fileRef.current?.click()} disabled={uploading}>
            {uploading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Upload className="mr-2 h-4 w-4" />
            )}
            {uploading ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </div>

      {/* Search bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/30 pointer-events-none" />
        <input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="Search across all media..."
          className="w-full pl-10 pr-4 py-2.5 bg-white/5 border border-white/10 rounded-lg text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-accent/50"
          data-testid="unified-media-search"
        />
      </div>

      {/* Source tabs */}
      <div className="flex gap-2 flex-wrap">
        {(
          [
            { key: "all", label: "All", icon: "" },
            {
              key: "library",
              label: `My Library${libraryStats ? ` (${libraryStats.count})` : ""}`,
              icon: "📁",
            },
            { key: "stock", label: "Pexels Stock", icon: "🌐" },
            { key: "generated", label: "AI Generated", icon: "✨" },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            onClick={() => setSourceTab(t.key as SourceTab)}
            className={cn(
              "px-3 py-1.5 rounded-lg text-xs transition-colors",
              sourceTab === t.key
                ? "bg-accent/20 border border-accent/40 text-accent"
                : "bg-white/5 border border-white/10 text-white/40 hover:bg-white/10",
            )}
            data-testid={`source-tab-${t.key}`}
          >
            {t.icon ? `${t.icon} ` : ""}
            {t.label}
          </button>
        ))}
      </div>

      {/* Type filter chips */}
      <div className="flex gap-2 flex-wrap">
        {(["all", "video", "photo"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className={cn(
              "px-2.5 py-1 rounded-full text-[10px] transition-colors",
              typeFilter === t
                ? "bg-white/10 text-white/80 border border-white/20"
                : "bg-white/5 text-white/40 hover:bg-white/10 border border-transparent",
            )}
            data-testid={`type-filter-${t}`}
          >
            {t === "all" ? "All types" : t === "video" ? "Video" : "Photo"}
          </button>
        ))}
      </div>

      {/* Results header */}
      {searchQuery && (
        <div className="text-[11px] text-white/40">
          Search results for "{searchQuery}" — {items.length} shown
          {sourceTab === "stock" && stockTotalResults > 0 && (
            <>
              {" "}
              ({stockTotalResults.toLocaleString()} total · page {page} of {stockTotalPages})
            </>
          )}
        </div>
      )}

      {/* Grid */}
      {loading ? (
        <div className="py-12 text-center">
          <Loader2 className="w-6 h-6 animate-spin mx-auto text-white/30" />
        </div>
      ) : items.length === 0 ? (
        <div className="py-16 text-center text-white/30 text-sm">
          {searchQuery
            ? "No results"
            : sourceTab === "stock"
              ? "Type a search to find Pexels stock media"
              : "Your library is empty — upload media or search for stock"}
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
          {items.map((item) => (
            <MediaTile
              key={`${item.source}-${item.type}-${item.id}`}
              item={item}
              onAdd={handleAdd}
              onDelete={isDeletable(item) ? handleRequestDelete : undefined}
            />
          ))}
        </div>
      )}

      {/* Pagination — only meaningful for stock tab when multi-page */}
      {showPagination && (
        <SmartPagination
          page={page}
          totalPages={stockTotalPages}
          onPageChange={handlePageChange}
        />
      )}

      {/* Confirm-delete dialog */}
      <Dialog
        open={!!pendingDelete}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this media file?</DialogTitle>
            <DialogDescription>
              This will permanently remove it from your library. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 mt-2">
            <Button variant="outline" onClick={() => setPendingDelete(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button
              className="bg-red-500 hover:bg-red-600 text-white"
              disabled={deleting}
              onClick={handleConfirmDelete}
            >
              {deleting ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Deleting…
                </>
              ) : (
                <>
                  <Trash2 className="w-3.5 h-3.5 mr-1.5" /> Delete
                </>
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
