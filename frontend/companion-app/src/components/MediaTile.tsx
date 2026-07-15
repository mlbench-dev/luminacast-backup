import { useCallback, useRef, useState } from "react";
import { Plus, Film, Trash2 } from "lucide-react";
import { cn } from "@/lib/cn";
import type { MediaItem } from "@/lib/mediaTypes";

interface MediaTileProps {
  item: MediaItem;
  onAdd: (item: MediaItem) => void;
  onDelete?: (item: MediaItem) => void;
}

function formatDuration(seconds?: number): string | null {
  if (!seconds || seconds <= 0) return null;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function sourceBadge(source: MediaItem["source"]): string {
  if (source === "library") return "📁";
  if (source === "stock") return "🌐";
  return "✨";
}

export function MediaTile({ item, onAdd, onDelete }: MediaTileProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [hovering, setHovering] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);

  const isVideo = item.type === "video";
  const posterUrl = item.thumbnail;
  const videoUrl = isVideo ? item.url || item.src : undefined;
  const duration = formatDuration(item.duration);

  // Library items are already in the user's collection — no "+ Add" needed.
  const showAdd = item.source !== "library";

  const handleMouseEnter = useCallback(() => {
    if (!isVideo || !videoUrl) return;
    setHovering(true);
    hoverTimerRef.current = setTimeout(() => {
      videoRef.current?.play().catch(() => {});
    }, 200);
  }, [isVideo, videoUrl]);

  const handleMouseLeave = useCallback(() => {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    setHovering(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
  }, []);

  const credit =
    item.source === "stock"
      ? `Pexels · ${item.photographer || "unknown"}`
      : item.name || "";

  // Treat empty-string posters as missing — Pexels video results occasionally
  // arrive with thumb="" when the upstream `image` and `video_pictures[]`
  // fields are both empty, which would otherwise render a broken-image icon.
  const hasPoster = !!posterUrl && !posterFailed;

  return (
    <div
      className="group relative rounded-xl overflow-hidden bg-white/5 border border-white/5"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <div className="relative aspect-video bg-black/30">
        {isVideo ? (
          <>
            {hasPoster ? (
              <img
                src={posterUrl}
                alt={item.name || ""}
                loading="lazy"
                onError={() => setPosterFailed(true)}
                className={cn(
                  "absolute inset-0 w-full h-full object-cover transition-opacity duration-200",
                  hovering ? "opacity-0" : "opacity-100",
                )}
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center">
                <Film className="w-6 h-6 text-white/30" />
              </div>
            )}
            {videoUrl && (
              <video
                ref={videoRef}
                src={videoUrl}
                muted
                playsInline
                preload="metadata"
                className={cn(
                  "absolute inset-0 w-full h-full object-cover transition-opacity duration-200",
                  hovering ? "opacity-100" : "opacity-0",
                )}
              />
            )}
          </>
        ) : hasPoster ? (
          <img
            src={posterUrl}
            alt={item.name || ""}
            loading="lazy"
            onError={() => setPosterFailed(true)}
            className="absolute inset-0 w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <Film className="w-6 h-6 text-white/30" />
          </div>
        )}

        {/* Source badge top-left */}
        <span
          className="absolute top-2 left-2 text-[10px] px-1.5 py-0.5 rounded-full bg-black/60 text-white/70 z-10"
          title={item.source}
        >
          {sourceBadge(item.source)}
        </span>

        {/* Duration badge bottom-right */}
        {duration && (
          <span className="absolute bottom-2 right-2 bg-black/70 text-[10px] text-white px-1.5 py-0.5 rounded z-10">
            {duration}
          </span>
        )}

        {/* Delete button — revealed on hover, mirrors the cast-card and
            LuminacastMediaPanel pattern. Only rendered when the parent
            supplies onDelete (i.e. the item is user-owned, not stock). */}
        {onDelete && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
              onDelete(item);
            }}
            onMouseDown={(e) => e.stopPropagation()}
            className="absolute top-2 right-2 p-1 rounded-md bg-black/60 text-white/70 hover:text-red-400 hover:bg-red-500/20 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity z-20"
            title="Delete from library"
            aria-label="Delete media"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}

        {/* Hover overlay with Add — only for items not yet in the user's library */}
        {showAdd && (
          <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-end justify-center pb-3 z-10">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onAdd(item);
              }}
              className="px-4 py-1.5 bg-accent rounded-lg text-xs font-medium text-white hover:bg-accent/80 transition-colors flex items-center gap-1.5 shadow-lg"
            >
              <Plus className="w-3.5 h-3.5" /> Add
            </button>
          </div>
        )}
      </div>

      <div className="p-1.5">
        <span className="text-[10px] text-white/40 truncate block" title={credit}>
          {item.source === "stock" && item.pexels_url ? (
            <>
              Pexels ·{" "}
              <a
                href={item.photographer_url || item.pexels_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-white/60 hover:text-accent hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {item.photographer || "unknown"}
              </a>
            </>
          ) : (
            credit || " "
          )}
        </span>
      </div>
    </div>
  );
}
