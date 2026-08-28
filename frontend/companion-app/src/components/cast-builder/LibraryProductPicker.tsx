import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Package, Check, Search, Loader2, Globe } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { productsApi } from "@/lib/api";
import type { ProductWithAssets } from "@/lib/types";
import { cn } from "@/lib/cn";
import { toast } from "@/hooks/useToast";

interface LibraryProductPickerProps {
  selectedProductIds: string[];
  onToggleProduct: (productId: string) => void;
  maxProducts?: number;
  minProducts?: number;
}

export function LibraryProductPicker({
  selectedProductIds,
  onToggleProduct,
  maxProducts = 8,
  minProducts = 1,
}: LibraryProductPickerProps) {
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["products", 1, search, "newest", "all"],
    queryFn: () => productsApi.list({ page: 1, per_page: 50, search, sort: "newest", filter: "all" }),
  });

  const products = data?.products || [];
  const selectedSet = new Set(selectedProductIds);

  const handleToggle = (productId: string) => {
    if (!selectedSet.has(productId) && selectedProductIds.length >= maxProducts) {
      toast({ title: `Max ${maxProducts} products`, variant: "warning" });
      return;
    }
    onToggleProduct(productId);
  };

  return (
    <div className="space-y-3" data-testid="library-product-picker">
      {/* Search */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { setSearch(searchInput); } }}
            placeholder="Search your product library..."
            className="pl-10"
          />
        </div>
        <Button variant="outline" onClick={() => setSearch(searchInput)}>
          <Search className="h-4 w-4" />
        </Button>
      </div>

      {/* Selection count */}
      <div className="flex items-center justify-between text-xs text-text-muted">
        <span>{products.length} products in library</span>
        <span className={cn(
          selectedProductIds.length < minProducts ? "text-orange-400" : "text-green-400"
        )}>
          {selectedProductIds.length} selected (min {minProducts}, max {maxProducts})
        </span>
      </div>

      {/* Product grid */}
      {isLoading ? (
        <div className="flex items-center justify-center gap-2 py-8">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
          <span className="text-sm text-text-dim">Loading products...</span>
        </div>
      ) : products.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border py-8 text-center space-y-3">
          <Package className="mx-auto mb-2 h-8 w-8 text-text-muted" />
          <p className="text-sm text-text-muted">
            No products in your library yet.
          </p>
          <div className="flex items-center justify-center gap-2">
            <a
              href="/products?tab=discover"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover transition-colors"
            >
              <Globe className="h-3.5 w-3.5" />
              Browse Discover
            </a>
            <a href="/products" className="text-xs text-accent hover:underline">
              or import manually
            </a>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 max-h-[400px] overflow-y-auto pr-1">
          {products.map((product) => {
            const selected = selectedSet.has(product.id);
            return (
              <button
                key={product.id}
                onClick={() => handleToggle(product.id)}
                className={cn(
                  "flex items-start gap-3 rounded-lg border p-3 text-left transition-all",
                  selected ? "border-accent bg-accent/5" : "border-border bg-surface hover:border-accent/30",
                )}
                data-testid={`library-product-${product.id}`}
              >
                {/* Cover image */}
                {product.cover_image_url ? (
                  <img
                    src={product.cover_image_url}
                    alt={product.name}
                    className="h-14 w-14 rounded object-cover shrink-0"
                    loading="lazy"
                    decoding="async"
                  />
                ) : (
                  <div className="h-14 w-14 rounded bg-card flex items-center justify-center shrink-0">
                    <Package className="h-5 w-5 text-text-muted" />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-text line-clamp-2">{product.name}</p>
                  <p className="text-xs text-text-dim mt-0.5">${product.price.toFixed(2)}</p>
                  {product.video_count > 0 && (
                    <span className="text-[10px] text-green-400">{product.video_count} videos</span>
                  )}
                </div>
                {/* Selection indicator */}
                <div className={cn(
                  "shrink-0 rounded-full h-5 w-5 flex items-center justify-center border",
                  selected ? "bg-accent border-accent" : "border-border"
                )}>
                  {selected && <Check className="h-3 w-3 text-white" />}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
