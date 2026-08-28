/**
 * Product carousel toggle — surfaces in the Script Phase block card whenever
 * the block has a product attached AND the product has more than one media
 * asset. Lets the user enable carousel mode (fade-through of all product
 * images during the block), pick a per-item speed (1.5-6s), choose a
 * transition style, and toggle individual assets in/out of the rotation.
 *
 * Persistence is via PUT /casts/{id}/blocks/{block_id} with a `metadata`
 * payload containing {product_carousel, carousel_speed_seconds,
 * carousel_transition, carousel_asset_ids}. Server-side validation ranges
 * speed to [1.5, 6] and pins transition to crossfade|slide|zoom.
 *
 * NOTE: drag-to-reorder is intentionally out of scope for v1 (TODO future).
 * Click a thumbnail to toggle its inclusion. Selected = full opacity,
 * deselected = opacity-30.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { GalleryHorizontal, Play } from "lucide-react";
import { castsApi, productsApi } from "@/lib/api";
import type { Block, BlockMetadata } from "@/lib/types";
import { cn } from "@/lib/cn";
import { toast } from "@/hooks/useToast";

interface ProductCarouselToggleProps {
  castId: string;
  block: Block;
  /** Called after a successful update so the parent can invalidate caches /
      refetch. The hook is fire-and-forget — local state is optimistic. */
  onUpdated?: () => void;
}

const TRANSITIONS: Array<{ key: "crossfade" | "slide" | "zoom"; label: string }> = [
  { key: "crossfade", label: "crossfade" },
  { key: "slide", label: "slide" },
  { key: "zoom", label: "zoom" },
];

export function ProductCarouselToggle({ castId, block, onUpdated }: ProductCarouselToggleProps) {
  const productId = block.product_id;
  // We rely on /products/{id} returning `assets`. PR #20 makes the asset
  // count > 1 by importing all media URLs; without it a typical product has
  // only the cover and the carousel section is hidden by the >1 guard.
  const { data: product } = useQuery({
    queryKey: ["product", productId, "assets"],
    queryFn: () => productsApi.get(productId as string),
    enabled: !!productId,
    staleTime: 60_000,
  });

  const assets = product?.assets || [];
  const meta = (block.metadata || {}) as BlockMetadata;
  const enabled = !!meta.product_carousel;
  const speed = typeof meta.carousel_speed_seconds === "number" ? meta.carousel_speed_seconds : 3;
  const transition = (meta.carousel_transition as "crossfade" | "slide" | "zoom") || "crossfade";

  // Default selection = all assets in their server order. If the user has
  // explicitly trimmed the list, honour that subset.
  const selectedSet = useMemo(() => {
    if (Array.isArray(meta.carousel_asset_ids) && meta.carousel_asset_ids.length > 0) {
      return new Set(meta.carousel_asset_ids);
    }
    return new Set(assets.map((a) => a.id));
  }, [meta.carousel_asset_ids, assets]);

  // Fewer than two assets: nothing to fade between, so don't show the section.
  if (!productId || assets.length < 2) return null;

  const persist = async (patch: Partial<BlockMetadata>) => {
    try {
      const next: BlockMetadata = {
        product_carousel: enabled,
        carousel_speed_seconds: speed,
        carousel_transition: transition,
        carousel_asset_ids: Array.isArray(meta.carousel_asset_ids) ? meta.carousel_asset_ids : undefined,
        ...patch,
      };
      await castsApi.updateBlock(castId, block.id, { metadata: next });
      onUpdated?.();
    } catch (err) {
      toast({ title: "Failed to update carousel", variant: "destructive" });
      throw err;
    }
  };

  const toggleAsset = (assetId: string) => {
    const current = Array.isArray(meta.carousel_asset_ids) && meta.carousel_asset_ids.length > 0
      ? meta.carousel_asset_ids
      : assets.map((a) => a.id);
    const next = current.includes(assetId)
      ? current.filter((id) => id !== assetId)
      : [...current, assetId];
    // Don't let the user deselect everything — at least one item must remain.
    if (next.length === 0) {
      toast({ title: "Keep at least one image in the carousel" });
      return;
    }
    void persist({ carousel_asset_ids: next });
  };

  return (
    <div className="mt-2 p-2.5 bg-white/[0.02] border border-white/[0.07] rounded-lg">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GalleryHorizontal className="w-3.5 h-3.5 text-white/30" />
          <span className="text-xs text-white/40">Product Carousel</span>
        </div>
        <button
          type="button"
          aria-pressed={enabled}
          aria-label="Toggle product carousel"
          onClick={() => void persist({ product_carousel: !enabled })}
          className={cn(
            "relative h-5 w-9 rounded-full transition-colors",
            enabled ? "bg-accent" : "bg-white/15",
          )}
        >
          <div
            className={cn(
              "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
              enabled ? "translate-x-4" : "translate-x-0.5",
            )}
          />
        </button>
      </div>

      {enabled && (
        <div className="mt-2 space-y-2">
          {/* Speed control */}
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-white/25 w-16">Speed</span>
            <input
              type="range"
              min={1.5}
              max={6}
              step={0.5}
              value={speed}
              onChange={(e) => void persist({ carousel_speed_seconds: parseFloat(e.target.value) })}
              className="flex-1 h-1 accent-accent"
            />
            <span className="text-[10px] text-white/30 w-8">{speed}s</span>
          </div>

          {/* Transition style */}
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-white/25 w-16">Transition</span>
            <div className="flex gap-1">
              {TRANSITIONS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => void persist({ carousel_transition: t.key })}
                  className={cn(
                    "text-[10px] px-2 py-0.5 rounded",
                    transition === t.key ? "bg-accent/20 text-accent" : "text-white/30 hover:text-white/50",
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Preview thumbnails — click to include/exclude. */}
          {/* TODO future: drag-to-reorder. For v1 we render in the asset list's
              server order with positional numbers; click toggles inclusion. */}
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {assets.map((asset, i) => {
              const selected = selectedSet.has(asset.id);
              return (
                <button
                  key={asset.id}
                  type="button"
                  onClick={() => toggleAsset(asset.id)}
                  aria-pressed={selected}
                  className={cn(
                    "w-12 h-16 rounded overflow-hidden flex-shrink-0",
                    "border border-white/10 relative cursor-pointer transition-opacity",
                    selected ? "opacity-100" : "opacity-30",
                  )}
                >
                  {asset.media_type === "video" ? (
                    <>
                      <video src={asset.r2_url} className="w-full h-full object-cover" muted preload="metadata" playsInline />
                      <Play className="absolute inset-0 m-auto w-3 h-3 text-white/60" />
                    </>
                  ) : (
                    <img src={asset.r2_url} className="w-full h-full object-cover" alt="" loading="lazy" decoding="async" />
                  )}
                  <span className="absolute bottom-0 left-0 right-0 bg-black/50 text-[8px] text-white/50 text-center">
                    {i + 1}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
