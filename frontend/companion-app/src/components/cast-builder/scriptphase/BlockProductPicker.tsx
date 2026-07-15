import { useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { castsApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import type { Product } from "@/lib/types";

interface BlockProductPickerProps {
  castId: string;
  blockId: string;
  /** Products attached to the cast (the m2m the user picked at setup). */
  products: Array<
    Pick<Product, "id" | "name"> & {
      cover_image_url?: string;
      cover_image_key?: string;
    }
  >;
  /** Currently pinned product id, if any. */
  selectedProductId?: string | null;
  onChange: (productId: string | null) => void;
}

function thumbUrl(p: { cover_image_url?: string; cover_image_key?: string }): string {
  if (p.cover_image_url) return p.cover_image_url;
  if (p.cover_image_key) return cdnUrl(p.cover_image_key) || "";
  return "";
}

/**
 * Per-block product pin chip-list. Renders one chip per product attached
 * to the cast; clicking a chip pins that product to the block via PATCH
 * /casts/{cast_id}/blocks/{block_id} { product_id }. Clicking the
 * already-pinned chip clears the pin.
 *
 * The pinned product is what the action-frame dispatcher and script
 * rewriter use as override over the cast's default product.
 */
export function BlockProductPicker({
  castId,
  blockId,
  products,
  selectedProductId,
  onChange,
}: BlockProductPickerProps) {
  const [saving, setSaving] = useState<string | null>(null);

  if (!products || products.length === 0) return null;

  const handlePick = async (productId: string | null) => {
    setSaving(productId ?? "__clear__");
    try {
      // Server expects empty string to clear; pass id otherwise.
      await castsApi.updateBlock(castId, blockId, {
        product_id: productId ?? "",
      });
      onChange(productId);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Failed to pin product";
      toast({ title: msg, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <div className="text-[10px] font-medium uppercase tracking-wider text-white/45">
          Featured product (optional)
        </div>
        {selectedProductId && (
          <button
            type="button"
            onClick={() => handlePick(null)}
            className="text-[10px] text-white/40 hover:text-white/70 flex items-center gap-0.5"
            title="Clear pinned product"
          >
            <X className="w-2.5 h-2.5" /> Clear
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {products.map((p) => {
          const isSelected = p.id === selectedProductId;
          const isSaving = saving === p.id;
          const url = thumbUrl(p);
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => !isSaving && handlePick(isSelected ? null : p.id)}
              disabled={!!saving}
              className={cn(
                "group flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] transition-colors",
                isSelected
                  ? "border-accent/60 bg-accent/15 text-white"
                  : "border-white/10 bg-white/[0.03] text-white/70 hover:border-white/25 hover:bg-white/[0.06]",
                saving && !isSaving && "opacity-50",
              )}
              title={isSelected ? "Click to unpin" : `Pin "${p.name}" to this block`}
            >
              {url ? (
                <img
                  src={url}
                  alt=""
                  className="h-4 w-4 rounded-sm object-cover"
                />
              ) : (
                <div className="h-4 w-4 rounded-sm bg-white/10" />
              )}
              <span className="truncate max-w-[140px]">{p.name}</span>
              {isSaving ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : isSelected ? (
                <Check className="w-3 h-3 text-accent" />
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
