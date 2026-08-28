import { useState } from "react";
import { ChevronLeft, ChevronRight, Package } from "lucide-react";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import type { ProductAsset, ProductWithAssets } from "@/lib/types";

interface ProductMediaGalleryProps {
  product: Pick<ProductWithAssets, "cover_image_key" | "cover_image_url"> & {
    name?: string;
  };
  /**
   * `undefined` = still loading (renders skeleton).
   * `[]` and no cover = empty placeholder.
   */
  assets: ProductAsset[] | undefined;
  className?: string;
}

interface MediaItem {
  url: string;
  type: string;
}

/**
 * Single product media tile that the user can step through with dots /
 * arrows. Used in product picker grids and other product card surfaces.
 *
 * Does NOT auto-cycle — that behaviour belongs to the timeline carousel
 * (lands in a follow-up PR). Here the user is browsing, not previewing
 * playback, so progression is explicit.
 */
export function ProductMediaGallery({
  product,
  assets,
  className,
}: ProductMediaGalleryProps) {
  const [current, setCurrent] = useState(0);

  if (assets === undefined) {
    return (
      <div
        className={cn(
          "relative w-full aspect-square rounded-xl overflow-hidden bg-white/5 animate-pulse",
          className,
        )}
      />
    );
  }

  const allMedia: MediaItem[] =
    assets.length > 0
      ? assets.map((a) => ({
          url: a.r2_url || cdnUrl(a.r2_key),
          type: a.media_type,
        }))
      : product.cover_image_url || product.cover_image_key
        ? [
            {
              url:
                product.cover_image_url || cdnUrl(product.cover_image_key || ""),
              type: "image",
            },
          ]
        : [];

  if (allMedia.length === 0) {
    return (
      <div
        className={cn(
          "relative w-full aspect-square rounded-xl overflow-hidden bg-white/5 flex items-center justify-center",
          className,
        )}
      >
        <Package className="w-8 h-8 text-white/20" />
      </div>
    );
  }

  const safeIndex = Math.min(current, allMedia.length - 1);
  const item = allMedia[safeIndex];

  return (
    <div
      className={cn(
        "relative w-full aspect-square rounded-xl overflow-hidden bg-white/5 group",
        className,
      )}
    >
      {item.type === "video" ? (
        <video
          src={item.url}
          muted
          autoPlay
          loop
          playsInline
          preload="metadata"
          className="w-full h-full object-cover"
        />
      ) : (
        <img
          src={item.url}
          alt={product.name || ""}
          className="w-full h-full object-cover"
          loading="lazy"
          decoding="async"
        />
      )}

      {allMedia.length > 1 && (
        <>
          <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex gap-1">
            {allMedia.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setCurrent(i);
                }}
                aria-label={`Show media ${i + 1} of ${allMedia.length}`}
                className={cn(
                  "h-1.5 rounded-full transition-all",
                  i === safeIndex ? "bg-white w-3" : "bg-white/40 w-1.5",
                )}
              />
            ))}
          </div>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setCurrent((safeIndex - 1 + allMedia.length) % allMedia.length);
            }}
            aria-label="Previous media"
            className="absolute left-1 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <ChevronLeft className="w-3 h-3 text-white" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setCurrent((safeIndex + 1) % allMedia.length);
            }}
            aria-label="Next media"
            className="absolute right-1 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <ChevronRight className="w-3 h-3 text-white" />
          </button>

          <div className="absolute top-2 right-2 bg-black/50 text-[10px] text-white/70 px-1.5 py-0.5 rounded">
            {safeIndex + 1}/{allMedia.length}
          </div>
        </>
      )}
    </div>
  );
}
