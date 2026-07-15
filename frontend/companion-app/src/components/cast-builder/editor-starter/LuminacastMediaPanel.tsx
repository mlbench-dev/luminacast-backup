/**
 * LuminacastMediaPanel — left panel for Editor Starter.
 * Unified tab structure: Videos | Photos | Music
 * Each with sub-tabs: My Media | Stock | Generated
 *
 * - My Media: user's own library
 * - Stock: Pexels for videos/photos, platform music for music
 * - Generated: AI-generated content
 *
 * Features: lazy-load, hover preview for videos, expand modal for video/photo,
 * double-click adds to timeline.
 */
import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import {
  api,
  userVideosApi,
  userPhotosApi,
  stockMediaApi,
  generatedVideosApi,
  generatedPhotosApi,
} from "@/lib/api";
import { Loader2, Search, Film, Image, Music, Maximize2, X, Plus, Upload, Trash2 } from "lucide-react";
import { cdnUrl } from "@/lib/cdn";
import { cn } from "@/lib/cn";
import { toast } from "@/hooks/useToast";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useWriteContext, useFps, useDimensions, useAssets } from "./utils/use-context";
import { addAssetToState } from "./state/actions/add-asset-to-state";
import { addItem } from "./state/actions/add-item";
import { generateRandomId } from "./utils/generate-random-id";
import type { VideoAsset, AudioAsset, ImageAsset } from "./assets/assets";
import type { ImageItem } from "./items/image/image-item-type";
import type { AudioItem } from "./items/audio/audio-item-type";
import type { VideoItem } from "./items/video/video-item-type";

/** Returns true if the URL points to a video file (by extension). Used to decide
 *  between <img> and <video> when rendering a tile poster, since the backend
 *  /api/user-videos endpoint returns the MP4 URL in the thumb field. */
function isVideoUrl(url: string | undefined | null): boolean {
  if (!url) return false;
  return /\.(mp4|webm|mov|m4v)(\?|#|$)/i.test(url);
}

/** Sanitize item name: strip R2 key paths and internal suffixes to produce human-readable labels */
function humanLabel(name: string | undefined, fallback: string): string {
  if (!name) return fallback;
  // If it looks like an R2 key (contains slashes or UUIDs), use the fallback
  if (name.includes("/") || /[0-9a-f]{8}-[0-9a-f]{4}-/.test(name)) return fallback;
  // Strip internal suffixes like _face, _voice, block_N_
  if (/^block_\d+_(face|voice|video)$/.test(name)) return fallback;
  return name;
}

type TopTab = "videos" | "photos" | "music";
type SubTab = "mine" | "stock" | "generated";

interface MediaItem {
  id: string;
  type: string;
  name?: string;
  url?: string;
  src?: string;
  thumb?: string;
  thumbnail?: string;
  duration?: number;
  duration_seconds?: number;
  width?: number;
  height?: number;
  r2_key?: string;
  source?: SubTab;
}

const TOP_TABS: { key: TopTab; label: string; icon: typeof Film }[] = [
  { key: "videos", label: "Videos", icon: Film },
  { key: "photos", label: "Photos", icon: Image },
  { key: "music", label: "Music", icon: Music },
];

const SUB_TABS: { key: SubTab; label: string }[] = [
  { key: "mine", label: "Uploaded" },
  { key: "stock", label: "Stock" },
  { key: "generated", label: "Generated" },
];

/** Items in "stock" sub-tab can't be deleted (third-party catalog). Generated
 *  music tracks are also non-deletable from this panel — they live in the
 *  Music studio and are tied to other generation flows. Everything else
 *  (uploaded videos/photos, generated videos/photos) is owned by the user
 *  and supports server-side soft-delete. */
function isDeletable(item: MediaItem, topTab: TopTab, subTab: SubTab): boolean {
  if (subTab === "stock") return false;
  if (topTab === "music") return false;
  return !!item.id;
}

export function LuminacastMediaPanel() {
  const [topTab, setTopTab] = useState<TopTab>("videos");
  const [subTab, setSubTab] = useState<SubTab>("mine");
  const [searchQuery, setSearchQuery] = useState("");
  const [items, setItems] = useState<MediaItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [fullscreenItem, setFullscreenItem] = useState<MediaItem | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  // Track of an in-progress delete confirm. Holds the item plus a precomputed
  // "in-use" flag so the dialog can show a warning when applicable.
  const [pendingDelete, setPendingDelete] = useState<{ item: MediaItem; inUse: boolean } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { setState } = useWriteContext();
  const { fps } = useFps();
  const { compositionWidth, compositionHeight } = useDimensions();
  const { assets } = useAssets();

  // Cheap lookup of asset keys/URLs currently referenced by the editor so we
  // can warn the user before they delete media that's in use in the cast.
  const usedAssetSet = useMemo(() => {
    const s = new Set<string>();
    for (const a of Object.values(assets || {})) {
      const key = (a as any).remoteFileKey;
      const url = (a as any).remoteUrl;
      if (typeof key === "string" && key) s.add(key);
      if (typeof url === "string" && url) s.add(url);
    }
    return s;
  }, [assets]);

  const isItemInUse = useCallback(
    (item: MediaItem): boolean => {
      if (item.r2_key && usedAssetSet.has(item.r2_key)) return true;
      const url = item.url || item.src;
      if (url && usedAssetSet.has(url)) return true;
      return false;
    },
    [usedAssetSet],
  );

  const fetchItems = useCallback(async (pageNum: number = 1, append: boolean = false) => {
    setLoading(true);
    try {
      let fetched: MediaItem[] = [];

      if (topTab === "videos") {
        if (subTab === "mine") {
          const data = await userVideosApi.list();
          fetched = (data.videos || []).map((v: any) => ({
            ...v, type: "video", thumb: v.thumbnail || v.url || (v.r2_key ? cdnUrl(v.r2_key) : ""), source: "mine" as SubTab,
          }));
        } else if (subTab === "stock") {
          if (!searchQuery) { setItems([]); setLoading(false); return; }
          const data = await stockMediaApi.searchVideos({ q: searchQuery, per_page: 15, page: pageNum });
          fetched = (data.results || []).map((v: any) => ({ ...v, source: "stock" as SubTab }));
          setHasMore((data.results || []).length >= 15);
        } else {
          const resp = await api.get("/videos/generated", { params: { page: pageNum, per_page: 20 } });
          fetched = ((resp.data as any).videos || []).map((v: any) => ({
            ...v, type: "video",
            url: v.url || (v.r2_key ? cdnUrl(v.r2_key) : ""),
            thumb: v.thumbnail || v.url || (v.r2_key ? cdnUrl(v.r2_key) : ""),
            source: "generated" as SubTab,
          }));
        }
      } else if (topTab === "photos") {
        if (subTab === "mine") {
          // User photos — stub as "Upload coming soon" if no endpoint
          try {
            const resp = await api.get("/user-photos", { params: { page: pageNum } });
            fetched = ((resp.data as any).photos || []).map((p: any) => ({
              ...p, type: "image", source: "mine" as SubTab,
            }));
          } catch {
            fetched = [];
          }
        } else if (subTab === "stock") {
          if (!searchQuery) { setItems([]); setLoading(false); return; }
          const data = await stockMediaApi.searchPhotos({ q: searchQuery, per_page: 20, page: pageNum });
          fetched = (data.results || []).map((p: any) => ({ ...p, source: "stock" as SubTab }));
          setHasMore((data.results || []).length >= 20);
        } else {
          const resp = await api.get("/photos/generated", { params: { page: pageNum, per_page: 20 } });
          fetched = ((resp.data as any).photos || []).map((p: any) => ({
            ...p, type: "image",
            url: p.url || (p.r2_key ? cdnUrl(p.r2_key) : ""),
            thumb: p.thumbnail || p.url || (p.r2_key ? cdnUrl(p.r2_key) : ""),
            source: "generated" as SubTab,
          }));
        }
      } else {
        // Music
        if (subTab === "mine") {
          try {
            const resp = await api.get("/user-music");
            fetched = ((resp.data as any).tracks || []).map((t: any) => ({
              ...t, type: "audio", url: t.audio_url, source: "mine" as SubTab,
            }));
          } catch {
            fetched = [];
          }
        } else if (subTab === "stock") {
          const resp = await api.get("/music/tracks");
          fetched = ((resp.data as any).tracks || []).map((t: any) => ({
            ...t, type: "audio", url: t.audio_url, source: "stock" as SubTab,
          }));
        } else {
          const resp = await api.get("/music/tracks", { params: { source: "generated" } });
          fetched = ((resp.data as any).tracks || []).map((t: any) => ({
            ...t, type: "audio", url: t.audio_url, source: "generated" as SubTab,
          }));
        }
      }

      if (append) {
        setItems(prev => [...prev, ...fetched]);
      } else {
        setItems(fetched);
      }
    } catch (err) {
      console.error("Media fetch failed:", err);
      if (!append) setItems([]);
    } finally {
      setLoading(false);
    }
  }, [topTab, subTab, searchQuery]);

  useEffect(() => {
    setPage(1);
    setHasMore(false);
    fetchItems(1);
  }, [topTab, subTab]);

  const handleSearch = useCallback(() => {
    setPage(1);
    fetchItems(1);
  }, [fetchItems]);

  const uploadEnabled = topTab === "videos" || topTab === "photos";
  const fileAccept = topTab === "videos" ? "video/*" : topTab === "photos" ? "image/*" : "";

  const handleUploadClick = useCallback(() => {
    if (!uploadEnabled) return;
    setUploadError(null);
    fileInputRef.current?.click();
  }, [uploadEnabled]);

  const handleFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setLoading(true);
    setUploadError(null);
    try {
      if (topTab === "videos") {
        await userVideosApi.upload(file);
      } else if (topTab === "photos") {
        await userPhotosApi.upload(file);
      } else {
        return;
      }
      if (subTab !== "mine") {
        setSubTab("mine");
      } else {
        await fetchItems(1, false);
      }
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || "Upload failed";
      setUploadError(typeof msg === "string" ? msg : "Upload failed");
      console.error("Upload failed:", err);
    } finally {
      setLoading(false);
    }
  }, [topTab, subTab, fetchItems]);

  // Infinite scroll — load more at 80%
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || loading || !hasMore) return;
    const threshold = el.scrollHeight * 0.8;
    if (el.scrollTop + el.clientHeight >= threshold) {
      const nextPage = page + 1;
      setPage(nextPage);
      fetchItems(nextPage, true);
    }
  }, [loading, hasMore, page, fetchItems]);

  // Escape to close fullscreen modal
  useEffect(() => {
    if (!fullscreenItem) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreenItem(null);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [fullscreenItem]);

  const needsSearch = subTab === "stock";

  const handleAddToTimeline = useCallback((item: MediaItem) => {
    const src = item.url || item.src || (item.r2_key ? cdnUrl(item.r2_key) : "");
    if (!src) return;

    const assetId = generateRandomId();
    const itemId = generateRandomId();
    const durationSec = item.duration || item.duration_seconds || 5;
    const durationInFrames = Math.ceil(durationSec * fps);

    if (item.type === "video") {
      setState({
        update: (state) => {
          const asset: VideoAsset = {
            id: assetId, type: "video",
            filename: humanLabel(item.name, "Video"),
            remoteUrl: src, remoteFileKey: item.r2_key || null,
            size: 0, mimeType: "video/mp4",
            durationInSeconds: durationSec, hasAudioTrack: false,
            width: item.width || compositionWidth,
            height: item.height || compositionHeight,
          };
          const videoItem: VideoItem = {
            id: itemId, type: "video",
            durationInFrames, from: 0,
            top: 0, left: 0,
            width: item.width || compositionWidth,
            height: item.height || compositionHeight,
            opacity: 1, isDraggingInTimeline: false,
            videoStartFromInSeconds: 0, decibelAdjustment: 0,
            playbackRate: 1,
            audioFadeInDurationInSeconds: 0, audioFadeOutDurationInSeconds: 0,
            fadeInDurationInSeconds: 0, fadeOutDurationInSeconds: 0,
            assetId, keepAspectRatio: true,
            borderRadius: 0, rotation: 0,
            cropLeft: 0, cropTop: 0, cropRight: 0, cropBottom: 0,
          };
          let s = addAssetToState({ state, asset });
          s = addItem({ state: s, item: videoItem, select: true, position: { type: "back" } });
          return s;
        },
        commitToUndoStack: true,
      });
    } else if (item.type === "photo" || item.type === "image") {
      setState({
        update: (state) => {
          const asset: ImageAsset = {
            id: assetId, type: "image",
            filename: humanLabel(item.name, "Photo"),
            remoteUrl: src, remoteFileKey: item.r2_key || null,
            size: 0, mimeType: "image/jpeg",
            width: item.width || compositionWidth,
            height: item.height || compositionHeight,
          };
          const imageItem: ImageItem = {
            id: itemId, type: "image",
            durationInFrames: Math.ceil(5 * fps), from: 0,
            top: 0, left: 0,
            width: item.width || compositionWidth,
            height: item.height || compositionHeight,
            opacity: 1, isDraggingInTimeline: false,
            assetId, keepAspectRatio: true,
            fadeInDurationInSeconds: 0, fadeOutDurationInSeconds: 0,
            borderRadius: 0, rotation: 0,
            cropLeft: 0, cropTop: 0, cropRight: 0, cropBottom: 0,
          };
          let s = addAssetToState({ state, asset });
          s = addItem({ state: s, item: imageItem, select: true, position: { type: "back" } });
          return s;
        },
        commitToUndoStack: true,
      });
    } else if (item.type === "audio") {
      setState({
        update: (state) => {
          const asset: AudioAsset = {
            id: assetId, type: "audio",
            filename: humanLabel(item.name, "Audio"),
            remoteUrl: src, remoteFileKey: item.r2_key || null,
            size: 0, mimeType: "audio/mpeg",
            durationInSeconds: durationSec,
          };
          const audioItem: AudioItem = {
            id: itemId, type: "audio",
            durationInFrames, from: 0,
            top: 0, left: 0, width: 100, height: 100,
            opacity: 1, isDraggingInTimeline: false,
            audioStartFromInSeconds: 0, decibelAdjustment: 0,
            playbackRate: 1,
            audioFadeInDurationInSeconds: 0, audioFadeOutDurationInSeconds: 0,
            assetId,
          };
          let s = addAssetToState({ state, asset });
          s = addItem({ state: s, item: audioItem, select: true, position: { type: "back" } });
          return s;
        },
        commitToUndoStack: true,
      });
    }
  }, [setState, fps, compositionWidth, compositionHeight]);

  const handleRequestDelete = useCallback((item: MediaItem) => {
    setPendingDelete({ item, inUse: isItemInUse(item) });
  }, [isItemInUse]);

  // Optimistic delete: drop the item from the local grid first, restore on
  // failure. Mirrors the cast-card delete pattern (PR #34) — soft-delete
  // happens server-side; R2 cleanup is best-effort there. The endpoint
  // depends on the (topTab, subTab) the item was loaded from.
  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const item = pendingDelete.item;
    if (!item.id) {
      setPendingDelete(null);
      return;
    }
    const previous = items;
    setDeleting(true);
    setItems(prev => prev.filter(i => i.id !== item.id));
    try {
      if (topTab === "videos" && subTab === "mine") {
        await userVideosApi.delete(item.id);
      } else if (topTab === "photos" && subTab === "mine") {
        await userPhotosApi.delete(item.id);
      } else if (topTab === "videos" && subTab === "generated") {
        await generatedVideosApi.delete(item.id);
      } else if (topTab === "photos" && subTab === "generated") {
        await generatedPhotosApi.delete(item.id);
      } else {
        throw new Error("This item cannot be deleted from here.");
      }
      toast({ title: "Media deleted" });
      setPendingDelete(null);
    } catch (err: any) {
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
  }, [pendingDelete, items, topTab, subTab]);

  return (
    <div className="flex flex-col h-full w-72 shrink-0 bg-editor-starter-panel text-white text-xs border-r border-editor-starter-border">
      {/* Top tabs: Videos | Photos | Music */}
      <div className="flex border-b border-editor-starter-border">
        {TOP_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => { setTopTab(tab.key); setSubTab("mine"); setSearchQuery(""); setItems([]); }}
            className={`flex-1 py-2 px-1 text-center text-[10px] transition-colors ${
              topTab === tab.key
                ? "text-editor-starter-accent border-b-2 border-editor-starter-accent"
                : "text-neutral-400 hover:text-white"
            }`}
          >
            <tab.icon className="w-3.5 h-3.5 mx-auto mb-0.5" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Sub-tabs: My Media | Stock | Generated + Upload button */}
      <div className="flex items-center border-b border-editor-starter-border">
        <div className="flex flex-1">
          {SUB_TABS.map((st) => (
            <button
              key={st.key}
              onClick={() => { setSubTab(st.key); setSearchQuery(""); setItems([]); }}
              className={`flex-1 py-1.5 text-center text-[10px] transition-colors ${
                subTab === st.key
                  ? "text-white bg-white/10 font-medium"
                  : "text-neutral-400 hover:text-white hover:bg-white/5"
              }`}
            >
              {st.label}
            </button>
          ))}
        </div>
        {/* Small + Upload button in corner */}
        <button
          onClick={handleUploadClick}
          disabled={!uploadEnabled}
          className="px-2 py-1.5 text-neutral-400 hover:text-white hover:bg-white/5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-neutral-400"
          title={uploadEnabled ? "Upload media" : "Upload coming soon"}
        >
          <Upload className="w-3.5 h-3.5" />
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept={fileAccept}
          className="hidden"
          onChange={handleFileSelected}
        />
      </div>
      {uploadError && (
        <div className="px-2 py-1 text-[10px] text-red-400 bg-red-500/10 border-b border-red-500/20">
          {uploadError}
        </div>
      )}

      {/* Search bar (Stock sub-tab only) */}
      {needsSearch && (
        <div className="p-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-neutral-400" />
            <input
              type="text"
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              className="w-full pl-7 pr-2 py-1.5 bg-editor-starter-bg border border-editor-starter-border rounded text-xs text-white placeholder:text-neutral-500 focus:outline-hidden focus:border-editor-starter-accent"
            />
          </div>
        </div>
      )}

      {/* Items grid */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-2"
      >
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-4 h-4 animate-spin text-neutral-400" />
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-8 text-neutral-400">
            {subTab === "mine" && topTab === "music" ? (
              "Upload coming soon"
            ) : subTab === "mine" && (topTab === "videos" || topTab === "photos") ? (
              `Upload your first ${topTab === "videos" ? "video" : "photo"}`
            ) : needsSearch && !searchQuery ? (
              "Type to search"
            ) : (
              "No items found"
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-1.5">
            {items.map((mediaItem) => (
              <MediaCard
                key={mediaItem.id}
                item={mediaItem}
                onAdd={handleAddToTimeline}
                onExpand={setFullscreenItem}
                onDelete={isDeletable(mediaItem, topTab, subTab) ? handleRequestDelete : undefined}
              />
            ))}
          </div>
        )}
        {loading && items.length > 0 && (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="w-4 h-4 animate-spin text-neutral-400" />
          </div>
        )}
      </div>

      {/* Fullscreen media modal — video or photo */}
      {fullscreenItem && (
        <FullscreenMediaModal
          item={fullscreenItem}
          onClose={() => setFullscreenItem(null)}
          onAdd={handleAddToTimeline}
        />
      )}

      {/* Confirm-delete dialog. Single instance for the panel; conditional
          render so unmounting clears Radix internal state. */}
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
          {pendingDelete?.inUse && (
            <div className="text-xs text-amber-400">
              Heads up: this media is used in your current cast. Deleting will leave broken references.
            </div>
          )}
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

/** Fullscreen modal for video/photo preview */
function FullscreenMediaModal({
  item,
  onClose,
  onAdd,
}: {
  item: MediaItem;
  onClose: () => void;
  onAdd: (item: MediaItem) => void;
}) {
  const src = item.url || item.src || (item.r2_key ? cdnUrl(item.r2_key) : "");
  // Treat the asset as a video either when typed as such, or when the URL
  // itself is a video file (defends against a video URL being rendered in
  // an <img> tag, which produces a broken-image icon).
  const isVideo = item.type === "video" || isVideoUrl(src);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="relative max-w-2xl w-full mx-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm text-white/60 truncate max-w-[300px]">
            {humanLabel(item.name, item.type || "Media")}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { onAdd(item); onClose(); }}
              className="flex items-center gap-1 text-sm text-accent hover:text-accent/80 transition-colors"
            >
              <Plus className="w-4 h-4" /> Add to timeline
            </button>
            <button onClick={onClose} className="text-white/60 hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
        {isVideo && src ? (
          <video
            src={src}
            controls
            autoPlay
            playsInline
            className="w-full rounded-xl"
          />
        ) : src ? (
          <img src={src} alt={item.name || ""} className="w-full rounded-xl object-contain max-h-[70vh]" />
        ) : null}
      </div>
    </div>
  );
}

/** Single media card with hover preview for videos, expand button, and click to add. */
function MediaCard({
  item,
  onAdd,
  onExpand,
  onDelete,
}: {
  item: MediaItem;
  onAdd: (item: MediaItem) => void;
  onExpand: (item: MediaItem) => void;
  onDelete?: (item: MediaItem) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [hovering, setHovering] = useState(false);

  const isVideo = item.type === "video";
  const isImage = item.type === "photo" || item.type === "image";

  // Posters use thumb/thumbnail (JPEG). Video src is in item.url || item.src.
  // Uploaded videos currently come back with thumb == the MP4 URL (no real
  // image thumbnail exists yet), so we detect that and treat it as a video
  // poster instead of an <img> source.
  const rawPoster = item.thumb || item.thumbnail;
  const posterIsImage = !!rawPoster && !isVideoUrl(rawPoster);
  const posterUrl = posterIsImage ? rawPoster : undefined;
  const videoUrl = isVideo ? (item.url || item.src || (isVideoUrl(rawPoster) ? rawPoster : undefined)) : undefined;
  const imageUrl = posterIsImage ? rawPoster : (item.url || item.src);

  const handleMouseEnter = useCallback(() => {
    if (item.type !== "video") return;
    setHovering(true);
    hoverTimerRef.current = setTimeout(() => {
      videoRef.current?.play().catch(() => {});
    }, 200);
  }, [item.type]);

  const handleMouseLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHovering(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
  }, []);

  // Only show source badge for non-uploaded sources (stock, generated) — "Uploaded" is redundant
  const sourceBadge = item.source && item.source !== "mine" ? (
    <span className="absolute top-1 right-1 text-[8px] px-1 py-0.5 rounded bg-black/60 text-white/70 capitalize z-10">
      {item.source}
    </span>
  ) : null;

  return (
    <div
      className="group relative aspect-video bg-neutral-900 rounded overflow-hidden border border-editor-starter-border hover:border-editor-starter-accent transition-colors"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Click to add to timeline */}
      <button
        onClick={() => onAdd(item)}
        onDoubleClick={() => onAdd(item)}
        className="absolute inset-0 w-full h-full"
      >
        {isVideo ? (
          <>
            {posterUrl && (
              <img
                src={posterUrl}
                alt={item.name || ""}
                className={cn(
                  "absolute inset-0 w-full h-full object-cover transition-opacity duration-200",
                  hovering ? "opacity-0" : "opacity-100"
                )}
                loading="lazy"
              />
            )}
            {videoUrl && (
              <video
                ref={videoRef}
                src={videoUrl}
                muted
                playsInline
                preload="metadata"
                onLoadedMetadata={(e) => {
                  // Seek to first frame so the resting <video> shows a poster-
                  // like frame instead of a black rectangle. Only do this when
                  // there is no separate image poster covering the video.
                  if (!posterUrl) {
                    const v = e.currentTarget;
                    try { v.currentTime = 0.1; } catch { /* ignore */ }
                  }
                }}
                className={cn(
                  "absolute inset-0 w-full h-full object-cover transition-opacity duration-200",
                  // When we have no image poster, the <video> IS the poster —
                  // keep it visible at rest and let hover trigger playback.
                  posterUrl ? (hovering ? "opacity-100" : "opacity-0") : "opacity-100"
                )}
              />
            )}
            {!posterUrl && !videoUrl && (
              <div className="absolute inset-0 flex items-center justify-center">
                <Film className="w-5 h-5 text-neutral-500" />
              </div>
            )}
          </>
        ) : isImage && imageUrl ? (
          <img
            src={imageUrl}
            alt={item.name || ""}
            className="absolute inset-0 w-full h-full object-cover transition-transform duration-300 group-hover:scale-110"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <Music className="w-5 h-5 text-neutral-400" />
          </div>
        )}
      </button>

      {sourceBadge}

      {/* Expand button (video and image only) */}
      {(isVideo || isImage) && (
        <button
          onClick={(e) => { e.stopPropagation(); onExpand(item); }}
          className="absolute top-1 left-1 p-0.5 rounded bg-black/60 text-white/70 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity z-10"
          title="Expand preview"
        >
          <Maximize2 className="w-3 h-3" />
        </button>
      )}

      {/* Delete button — revealed on hover, mirrors cast-card pattern.
          Stops propagation so the tile's primary action (add to timeline)
          doesn't fire. Only rendered when the parent supplies onDelete
          (i.e. this is a user-owned source, not stock). */}
      {onDelete && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); e.preventDefault(); onDelete(item); }}
          onMouseDown={(e) => e.stopPropagation()}
          className="absolute bottom-1 right-1 p-0.5 rounded bg-black/60 text-white/70 hover:text-red-400 hover:bg-red-500/20 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity z-10"
          title="Delete from library"
          aria-label="Delete media"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      )}

      <div className="absolute inset-x-0 bottom-0 bg-linear-to-t from-black/70 to-transparent p-1 pointer-events-none">
        <span className="text-[9px] text-white/90 line-clamp-1">{humanLabel(item.name, item.type || "Media")}</span>
        {(item.duration || item.duration_seconds) && (
          <span className="text-[8px] text-white/60">
            {Math.round(item.duration || item.duration_seconds || 0)}s
          </span>
        )}
      </div>
      <div className="absolute inset-0 bg-editor-starter-accent/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center pointer-events-none">
        <span className="text-[10px] text-white font-medium">+ Add</span>
      </div>
    </div>
  );
}
