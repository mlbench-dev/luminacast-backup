/**
 * Visual source picker — surfaces in the Script Phase block card for
 * stock_photo/stock_video blocks with a product attached. These blocks are
 * pure B-roll (no avatar face appears — see tasks/cast_render.py); until
 * now their visual was fully automatic (Pexels search, no user control).
 *
 * Lets the user pick between:
 *   - one of the product's own uploaded/AI-generated photos or videos
 *     (already fetched via GET /products/{id} — same data ProductCarouselToggle
 *     uses), assigned via Block.image_asset_id / video_asset_id, which
 *     outranks the Pexels stock_media_url at render time (see
 *     resolve_voiceover_visual_sources in tasks/cast_render.py).
 *   - generic stock (Pexels) — clears the override, falling back to the
 *     auto-populated stock_media_url.
 *   - "Generate new" — calls the same AI generation endpoints as the
 *     Product page's own AI Generate section, then assigns the result.
 *
 * Persistence is via PUT /casts/{id}/blocks/{block_id} with
 * image_asset_id/video_asset_id — the same endpoint ProductCarouselToggle
 * uses for `metadata`.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageIcon, Loader2, Play, Sparkles } from "lucide-react";
import { castsApi, productsApi } from "@/lib/api";
import type { Block } from "@/lib/types";
import { cn } from "@/lib/cn";
import { toast } from "@/hooks/useToast";

interface VisualSourcePickerProps {
  castId: string;
  block: Block;
  onUpdated?: () => void;
}

export function VisualSourcePicker({ castId, block, onUpdated }: VisualSourcePickerProps) {
  const productId = block.product_id;
  const wantsPhoto = block.category === "stock_photo";
  const [generating, setGenerating] = useState(false);
  const queryClient = useQueryClient();

  const { data: product } = useQuery({
    queryKey: ["product", productId, "assets"],
    queryFn: () => productsApi.get(productId as string),
    enabled: !!productId,
    staleTime: 60_000,
  });

  if (!productId) {
    return (
      <div className="mt-2 p-2.5 bg-white/[0.02] border border-white/[0.07] rounded-lg text-xs text-white/40">
        Stock media will be auto-selected from Pexels based on script content
      </div>
    );
  }

  const assets = (product?.assets || []).filter((a) =>
    wantsPhoto ? a.media_type === "image" : a.media_type === "video",
  );
  const selectedAssetId = wantsPhoto ? block.image_asset_id : block.video_asset_id;
  const usingStock = !selectedAssetId;

  const refresh = () => {
    onUpdated?.();
    void queryClient.invalidateQueries({ queryKey: ["cast", castId] });
  };

  const selectAsset = async (assetId: string | null) => {
    try {
      await castsApi.updateBlock(castId, block.id, {
        image_asset_id: wantsPhoto ? assetId : null,
        video_asset_id: wantsPhoto ? null : assetId,
      });
      refresh();
    } catch {
      toast({ title: "Failed to update visual source", variant: "destructive" });
    }
  };

  const generate = async () => {
    setGenerating(true);
    try {
      const result = wantsPhoto
        ? await productsApi.generateAiImages(productId, "lifestyle")
        : await productsApi.generateAiVideo(productId, "product_showcase", 5, "", "pro");
      await castsApi.updateBlock(castId, block.id, {
        image_asset_id: wantsPhoto ? result.id : null,
        video_asset_id: wantsPhoto ? null : result.id,
      });
      void queryClient.invalidateQueries({ queryKey: ["product", productId, "assets"] });
      refresh();
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast({
        title: "AI generation failed",
        description: msg || "Please try again in a moment.",
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="mt-2 p-2.5 bg-white/[0.02] border border-white/[0.07] rounded-lg space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-white/40">Visual source</span>
        <button
          type="button"
          onClick={() => void generate()}
          disabled={generating}
          className={cn(
            "flex items-center gap-1 text-[10px] px-2 py-0.5 rounded",
            "text-accent hover:bg-accent/10 disabled:opacity-50",
          )}
        >
          {generating ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Sparkles className="w-3 h-3" />
          )}
          {generating
            ? wantsPhoto ? "Generating…" : "Generating… (3-4 min)"
            : "Generate new"}
        </button>
      </div>

      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {/* Generic stock (Pexels) — clears the override. */}
        <button
          type="button"
          onClick={() => void selectAsset(null)}
          aria-pressed={usingStock}
          className={cn(
            "w-12 h-16 rounded overflow-hidden flex-shrink-0 relative",
            "border transition-opacity flex flex-col items-center justify-center gap-1 bg-white/5",
            usingStock ? "opacity-100 border-accent" : "opacity-40 border-white/10",
          )}
        >
          {block.stock_media_thumbnail ? (
            <img src={block.stock_media_thumbnail} className="w-full h-full object-cover" alt="" loading="lazy" decoding="async" />
          ) : (
            <ImageIcon className="w-4 h-4 text-white/40" />
          )}
          <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-[7px] text-white/60 text-center leading-tight py-0.5">
            Stock
          </span>
        </button>

        {/* Product's own uploaded/AI-generated assets. */}
        {assets.map((asset) => {
          const selected = selectedAssetId === asset.id;
          return (
            <button
              key={asset.id}
              type="button"
              onClick={() => void selectAsset(asset.id)}
              aria-pressed={selected}
              className={cn(
                "w-12 h-16 rounded overflow-hidden flex-shrink-0 relative",
                "border transition-opacity",
                selected ? "opacity-100 border-accent" : "opacity-40 border-white/10",
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
              {asset.asset_type?.startsWith("ai_generated") && (
                <span className="absolute top-0 left-0 right-0 bg-accent/70 text-[6px] text-white text-center leading-tight">
                  AI
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
