import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Search,
  Loader2,
  Star,
  ShoppingCart,
  Check,
  TrendingUp,
  Tag,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { tiktokShopApi } from "@/lib/api";
import type { TikTokShopProduct } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";

type SortKey = "best_selling" | "price_asc" | "price_desc" | "rating";

function formatSalesVolume(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M sold`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K sold`;
  return `${n} sold`;
}

function sortProducts(products: TikTokShopProduct[], key: SortKey): TikTokShopProduct[] {
  const sorted = [...products];
  switch (key) {
    case "best_selling":
      return sorted.sort((a, b) => b.sales_volume - a.sales_volume);
    case "price_asc":
      return sorted.sort((a, b) => parseFloat(a.current_price) - parseFloat(b.current_price));
    case "price_desc":
      return sorted.sort((a, b) => parseFloat(b.current_price) - parseFloat(a.current_price));
    case "rating":
      return sorted.sort((a, b) => b.rating - a.rating);
    default:
      return sorted;
  }
}

interface TikTokShopBrowserProps {
  selectedProducts: TikTokShopProduct[];
  onToggleProduct: (product: TikTokShopProduct) => void;
  onContinue: () => void;
}

export function TikTokShopBrowser({
  selectedProducts,
  onToggleProduct,
  onContinue,
}: TikTokShopBrowserProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TikTokShopProduct[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>("best_selling");
  const [proxyingImages, setProxyingImages] = useState<Set<string>>(new Set());

  const searchMutation = useMutation({
    mutationFn: (q: string) => tiktokShopApi.search(q, 50),
    onSuccess: async (data) => {
      setResults(data.products);
      // Proxy images in background
      for (const product of data.products) {
        if (product.image_urls.length > 0) {
          proxyImage(product.product_id, product.image_urls[0]);
        }
      }
    },
    onError: (e: any) =>
      toast({
        title: "Search failed",
        description: e?.response?.data?.detail || "Try again",
        variant: "destructive",
      }),
  });

  const proxyImage = async (productId: string, imageUrl: string) => {
    setProxyingImages((prev) => new Set(prev).add(productId));
    try {
      const data = await tiktokShopApi.proxyImage(imageUrl);
      setResults((prev) =>
        prev.map((p) =>
          p.product_id === productId
            ? { ...p, image_urls: [data.image_url, ...p.image_urls.slice(1)] }
            : p,
        ),
      );
    } catch {
      // Silently fail — original image URL will be used
    } finally {
      setProxyingImages((prev) => {
        const next = new Set(prev);
        next.delete(productId);
        return next;
      });
    }
  };

  const selectedIds = new Set(selectedProducts.map((p) => p.product_id));
  const sorted = sortProducts(results, sortKey);
  const isOutOfStock = (p: TikTokShopProduct) => p.sales_volume === 0;

  return (
    <div className="space-y-4" data-testid="tiktok-shop-browser">
      {/* Search bar */}
      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search TikTok Shop products..."
          onKeyDown={(e) => e.key === "Enter" && query.trim() && searchMutation.mutate(query)}
          data-testid="shop-search-input"
        />
        <Button
          onClick={() => query.trim() && searchMutation.mutate(query)}
          disabled={!query.trim() || searchMutation.isPending}
          data-testid="shop-search-button"
        >
          {searchMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Search className="h-4 w-4" />
          )}
        </Button>
        <Button
          variant="outline"
          onClick={() => searchMutation.mutate("trending beauty skincare")}
          disabled={searchMutation.isPending}
        >
          <TrendingUp className="mr-1 h-4 w-4" />
          Trending
        </Button>
      </div>

      {/* Sort */}
      {results.length > 0 && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">{results.length} products</span>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="rounded border border-border bg-surface px-2 py-1 text-xs text-text"
            data-testid="shop-sort-select"
          >
            <option value="best_selling">Best selling</option>
            <option value="price_asc">Price: Low → High</option>
            <option value="price_desc">Price: High → Low</option>
            <option value="rating">Highest rated</option>
          </select>
        </div>
      )}

      {/* Product grid */}
      {searchMutation.isPending && (
        <div className="flex items-center justify-center gap-2 py-12">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
          <span className="text-sm text-text-dim">Searching TikTok Shop...</span>
        </div>
      )}

      {results.length > 0 && (
        <div className="grid grid-cols-2 gap-3 max-h-[500px] overflow-y-auto pr-1">
          {sorted.map((product) => {
            const selected = selectedIds.has(product.product_id);
            const outOfStock = isOutOfStock(product);
            return (
              <div
                key={product.product_id}
                className={cn(
                  "rounded-lg border p-3 transition-all",
                  selected ? "border-accent bg-accent/5" : "border-border bg-surface",
                  outOfStock && "opacity-50",
                )}
                data-testid={`shop-product-${product.product_id}`}
              >
                {/* Image */}
                {product.image_urls[0] && (
                  <div className="mb-2 aspect-square overflow-hidden rounded-md bg-black/10">
                    <img
                      src={product.image_urls[0]}
                      alt={product.title}
                      className="h-full w-full object-cover"
                    />
                  </div>
                )}

                {/* Title */}
                <p className="text-xs font-medium text-text line-clamp-2 mb-1">{product.title}</p>

                {/* Seller + rating */}
                <div className="flex items-center gap-1 text-[10px] text-text-muted mb-1">
                  <span>{product.seller_name}</span>
                  {product.rating > 0 && (
                    <>
                      <span>·</span>
                      <Star className="h-2.5 w-2.5 fill-yellow-400 text-yellow-400" />
                      <span>{product.rating.toFixed(1)}</span>
                      <span>({product.review_count})</span>
                    </>
                  )}
                </div>

                {/* Price */}
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-bold text-accent">{product.current_price}</span>
                  {product.original_price && product.original_price !== product.current_price && (
                    <span className="text-[10px] text-text-muted line-through">
                      {product.original_price}
                    </span>
                  )}
                  {product.discount_percent > 0 && (
                    <Badge variant="danger" className="text-[10px] px-1 py-0">
                      -{product.discount_percent}%
                    </Badge>
                  )}
                </div>

                {/* Sales + tags */}
                <div className="flex flex-wrap items-center gap-1 mb-2">
                  {product.sales_volume > 0 && (
                    <span className="text-[10px] text-text-muted">
                      {formatSalesVolume(product.sales_volume)}
                    </span>
                  )}
                  {product.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="text-[10px] px-1 py-0">
                      <Tag className="mr-0.5 h-2 w-2" />
                      {tag}
                    </Badge>
                  ))}
                </div>

                {/* Add button */}
                <Button
                  size="sm"
                  variant={selected ? "default" : "outline"}
                  className="w-full text-xs"
                  disabled={outOfStock}
                  onClick={() => onToggleProduct(product)}
                  data-testid={`shop-add-${product.product_id}`}
                >
                  {outOfStock ? (
                    "Out of stock"
                  ) : selected ? (
                    <><Check className="mr-1 h-3 w-3" /> Added</>
                  ) : (
                    <><ShoppingCart className="mr-1 h-3 w-3" /> Add to Cast</>
                  )}
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {/* Empty state */}
      {!searchMutation.isPending && results.length === 0 && (
        <div className="rounded-lg border border-dashed border-border py-12 text-center">
          <ShoppingCart className="mx-auto mb-2 h-8 w-8 text-text-muted" />
          <p className="text-sm text-text-muted">Search TikTok Shop to find products for your cast</p>
        </div>
      )}

      {/* Cart bar */}
      {selectedProducts.length > 0 && (
        <div className="sticky bottom-0 flex items-center justify-between rounded-lg border border-accent bg-accent/10 p-3">
          <span className="text-sm text-text">
            {selectedProducts.length} product{selectedProducts.length !== 1 ? "s" : ""} selected
          </span>
          <Button size="sm" onClick={onContinue} data-testid="shop-continue-button">
            Continue to scripts →
          </Button>
        </div>
      )}
    </div>
  );
}
