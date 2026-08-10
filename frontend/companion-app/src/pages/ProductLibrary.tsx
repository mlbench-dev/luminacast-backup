import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { productsApi, discoverApi, isNeedsManualEntry } from "@/lib/api";
import type { NeedsManualEntry } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import type { ProductWithAssets, ProductAsset, ProductDetailResponse, DiscoverProduct, CategoryTree } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { buildOutboundProductUrl } from "@/lib/affiliateLink";
import { Sentry } from "@/lib/sentry";
import { confirmAction } from "@/lib/swal";
import {
  Plus,
  Search,
  ChevronLeft,
  ChevronRight,
  Package,
  Upload,
  Video,
  Image,
  Sparkles,
  Tag,
  Star,
  Film,
  X,
  Trash2,
  Pencil,
  ChevronDown,
  ChevronUp,
  TrendingUp,
  Flame,
  Zap,
  Award,
  ShoppingBag,
  Filter,
  RotateCcw,
  ExternalLink,
  ImageIcon,
  CheckCircle2,
  XCircle,
  Camera,
  ZoomIn,
  Scissors,
  Loader2,
  Link,
  Globe,
  MessageSquare,
  AlertCircle,
} from "lucide-react";

// ── Helpers ──

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatRevenue(cents: number): string {
  if (!cents) return "\u2014";
  const dollars = cents / 100;
  if (dollars >= 1_000_000) return `$${(dollars / 1_000_000).toFixed(2)}m`;
  if (dollars >= 1_000) return `$${(dollars / 1_000).toFixed(1)}k`;
  return `$${dollars.toFixed(0)}`;
}

// Sparkline removed (Phase 3.3 — PD-003)

function DiscoverTileImage({ url, name, className }: { url?: string; name?: string; className?: string }) {
  const [errored, setErrored] = useState(false);
  if (!url || errored) {
    return (
      <div className={`${className} bg-surface flex items-center justify-center`} title={name}>
        <Package className="h-5 w-5 text-text-muted" />
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={name || ""}
      className={`${className} object-cover bg-surface`}
      loading="lazy"
      onError={() => setErrored(true)}
    />
  );
}



// Sparkline removed (PD-003)

// ── Section tabs config ──

const SECTIONS = [
  { key: "top_selling", label: "Hot Selling", icon: Flame },
  { key: "trending", label: "Trending", icon: TrendingUp },
  { key: "flash_sale", label: "Flash Sale", icon: Zap },
  { key: "new", label: "New Products", icon: ShoppingBag },
  { key: "high_potential", label: "High Potential", icon: Award },
] as const;

// ── Source badge helper ──

// Resolve a product URL to a human-friendly link label that matches the
// store. "Open on TikTok" was hardcoded — swap for the real source so
// Amazon products say "Open on Amazon", Shopify says "Open on Shopify",
// generic stores say "Open product page".
function productLinkLabel(url: string): string {
  let host = "";
  try { host = new URL(url).hostname.toLowerCase(); } catch { return "Open product page"; }
  if (host.includes("tiktok")) return "Open on TikTok";
  if (host.includes("amazon") || host.includes("amzn")) return "Open on Amazon";
  if (host.includes("shopify") || host.includes("myshopify")) return "Open on Shopify";
  if (host.includes("etsy")) return "Open on Etsy";
  if (host.includes("shopee")) return "Open on Shopee";
  if (host.includes("aliexpress")) return "Open on AliExpress";
  if (host.includes("walmart")) return "Open on Walmart";
  if (host.includes("ebay")) return "Open on eBay";
  // Strip leading www. and a trailing .com/.co.uk/etc. for a clean fallback.
  const clean = host.replace(/^www\./, "").replace(/\.[a-z.]+$/, "");
  return clean ? `Open on ${clean.charAt(0).toUpperCase()}${clean.slice(1)}` : "Open product page";
}

// Resolve a "source label" for the top-left badge from commission_source
// when present, falling back to URL hostname for legacy rows that pre-date
// PR #23. Returns null if neither source nor URL identifies the platform —
// nothing to render. Bad URLs are caught (try/catch around new URL).
function sourceLabel(commissionSource?: string | null, url?: string): string | null {
  if (commissionSource === "tiktok_affiliate") return "TikTok Shop";
  if (commissionSource === "amazon_associates") return "Amazon";
  if (commissionSource === "manual" && url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return null; }
  }
  // Legacy rows with no commission_source — fall back to URL host detection
  // so old products don't lose their badge after PR #23 lands.
  if (url) {
    let host = "";
    try { host = new URL(url).hostname.toLowerCase(); } catch { return null; }
    if (host.includes("tiktok")) return "TikTok Shop";
    if (host.includes("amazon") || host.includes("amzn")) return "Amazon";
    return host.replace(/^www\./, "");
  }
  return null;
}

function SourceBadge({ commissionSource, url }: { commissionSource?: string | null; url?: string }) {
  const label = sourceLabel(commissionSource, url);
  if (!label) return null;
  // Match TikTok / Amazon brand chips for legibility; fall back to neutral.
  const isTikTok = label === "TikTok Shop" || /tiktok/i.test(label);
  const isAmazon = label === "Amazon" || /amazon|amzn/i.test(label);
  if (isTikTok) {
    return (
      <span className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-black/70 px-1.5 py-0.5 text-[9px] font-medium text-white backdrop-blur-sm">
        <svg viewBox="0 0 24 24" className="w-3 h-3" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.12v-3.49a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.16 15.8a6.34 6.34 0 0010.86 4.47V13.4a8.28 8.28 0 005.57 2.14v-3.44a4.85 4.85 0 01-3.57-1.98V6.69h3.57z" /></svg>
        {label}
      </span>
    );
  }
  if (isAmazon) {
    return (
      <span className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-[#FF9900]/90 px-1.5 py-0.5 text-[9px] font-medium text-black backdrop-blur-sm">
        {label}
      </span>
    );
  }
  return (
    <span className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-black/70 px-1.5 py-0.5 text-[9px] font-medium text-white backdrop-blur-sm">
      <Globe className="w-2.5 h-2.5" />
      {label}
    </span>
  );
}

// Small green commission chip used on cards. Uses the project's existing
// `bg-success` token for theme consistency (the spec's `bg-emerald-500/90`
// would look out of place against the rest of the app's green accents).
// Hidden when commission_rate is missing, zero, or negative.
function CommissionBadge({
  commissionRate,
  price,
  commissionSource,
  className = "absolute top-2 right-2",
}: {
  commissionRate?: number | null;
  price?: number | null;
  commissionSource?: string | null;
  className?: string;
}) {
  if (commissionRate == null || commissionRate <= 0) return null;
  const pct = Math.round(commissionRate * 100);
  const dollars = (price != null && price > 0) ? (price * commissionRate).toFixed(2) : null;
  const tooltip = commissionSource === "amazon_associates"
    ? "Amazon Associates rate (set by Amazon)"
    : commissionSource === "tiktok_affiliate"
      ? "TikTok Shop affiliate commission"
      : "Affiliate commission";
  return (
    <span
      title={tooltip}
      className={`${className} rounded-full bg-success/90 px-2 py-0.5 text-[10px] font-bold text-white backdrop-blur-sm shadow-sm`}
    >
      {pct}%{dollars != null ? ` · $${dollars}` : ""}
    </span>
  );
}


// ── URL Import Bar ──

export function UrlImportBar() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [manualEntry, setManualEntry] = useState<NeedsManualEntry | null>(null);
  const [needsImage, setNeedsImage] = useState<{ id: string; name: string } | null>(null);
  const [needsPrice, setNeedsPrice] = useState<{ id: string; name: string } | null>(null);

  const handleImport = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setImporting(true);
    try {
      const result = await productsApi.fromUrl(trimmed);
      if (isNeedsManualEntry(result)) {
        // Source blocked the automated lookup (e.g. TikTok Shop CAPTCHA, or a
        // generic site's bot-protection). Drop the user into a manual-entry
        // form pre-filled with the origin. This is NOT a success — surface an
        // informational toast with the backend's actual reason so the user
        // understands why the dialog opened, instead of a generic "it failed".
        setManualEntry(result);
        toast({
          title: "Couldn't fetch automatically",
          description: result.message,
          variant: "warning",
        });
        setImporting(false);
        return;
      }
      if ((result as any).already_existed) {
        toast({ title: "Already in your library", description: result.name });
      } else {
        toast({ title: "Product imported!", description: result.name });
        // The resolver couldn't fetch a usable image (bot-blocked source,
        // no og:image, etc.) — don't leave the product with a blank cover
        // silently; ask the user to add one right away.
        if (!(result as any).cover_image_url) {
          setNeedsImage({ id: (result as any).id, name: result.name });
        }
        // Same idea for price: some sites (Alibaba tiered/negotiated pricing,
        // pages with no product:price:amount meta tag, etc.) resolve fine —
        // image, title — but have no single price to extract, which the
        // backend stores as 0 rather than blocking the whole import. Don't
        // leave that silent; prompt for a real price right away.
        if (!(result as any).price) {
          setNeedsPrice({ id: (result as any).id, name: result.name });
        }
      }
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setUrl("");
    } catch (err: any) {
      toast({
        title: "Import failed",
        description: err?.response?.data?.detail || err.message || "Could not resolve product",
        variant: "destructive",
      });
    } finally {
      setImporting(false);
    }
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Link className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleImport()}
            placeholder="Paste any product URL (TikTok, Amazon, Shopify, ...)"
            className="pl-10"
            disabled={importing}
          />
        </div>
        <Button onClick={handleImport} disabled={!url.trim() || importing} size="sm" className="gap-2">
          {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          {importing ? "Importing..." : "Import"}
        </Button>
      </div>
      {manualEntry && (
        <ManualEntryDialog
          entry={manualEntry}
          onClose={() => setManualEntry(null)}
          onCreated={(name) => {
            setManualEntry(null);
            setUrl("");
            toast({ title: "Product added!", description: name });
            queryClient.invalidateQueries({ queryKey: ["products"] });
          }}
        />
      )}
      {needsImage && (
        <AddCoverImageDialog
          productId={needsImage.id}
          productName={needsImage.name}
          onClose={() => setNeedsImage(null)}
          onUploaded={() => {
            setNeedsImage(null);
            toast({ title: "Cover image added" });
            queryClient.invalidateQueries({ queryKey: ["products"] });
          }}
        />
      )}
      {needsPrice && (
        <SetPriceDialog
          productId={needsPrice.id}
          productName={needsPrice.name}
          onClose={() => setNeedsPrice(null)}
          onSaved={() => {
            setNeedsPrice(null);
            toast({ title: "Price set" });
            queryClient.invalidateQueries({ queryKey: ["products"] });
          }}
        />
      )}
    </>
  );
}


// ── Manual-entry fallback form ──
// Shown when the URL importer can't reach a source (e.g. TikTok Shop's
// CAPTCHA). Pre-fills the origin so the created product still links back,
// and collects the fields the resolver couldn't fetch.
function ManualEntryDialog({
  entry,
  onClose,
  onCreated,
}: {
  entry: NeedsManualEntry;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [description, setDescription] = useState("");
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      const product = await productsApi.create({
        name: name.trim(),
        price: parseFloat(price) || 0,
        description: description.trim() || undefined,
        source: entry.source,
        source_url: entry.source_url,
        source_product_id: entry.source_product_id ?? undefined,
      } as any);
      if (coverFile) {
        try {
          await productsApi.uploadAsset(product.id, coverFile, "product_shot");
        } catch (uploadErr: any) {
          Sentry.captureException(uploadErr);
          toast({
            title: "Product saved, image upload failed",
            description: "You can add a cover image later from the product page.",
            variant: "destructive",
          });
        }
      }
      onCreated(product.name || name.trim());
    } catch (err: any) {
      Sentry.captureException(err);
      toast({
        title: "Couldn't save product",
        description: err?.response?.data?.detail || err.message || "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold text-text">Add product details</h2>
        <p className="mt-1 text-sm text-text-muted">
          {entry.message} Fill in the details below and upload a cover image.
        </p>
        <div className="mt-4 space-y-3">
          <div>
            <label className="text-xs font-medium text-text-muted">Product name</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Product name" autoFocus />
          </div>
          <div>
            <label className="text-xs font-medium text-text-muted">Price (USD)</label>
            <Input
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="0.00"
              inputMode="decimal"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-text-muted">Description</label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description (optional)"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-text-muted">Cover image</label>
            <Input
              type="file"
              accept="image/*"
              onChange={(e) => setCoverFile(e.target.files?.[0] ?? null)}
            />
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!name.trim() || saving} className="gap-2">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {saving ? "Saving..." : "Add product"}
          </Button>
        </div>
      </div>
    </div>
  );
}


// ── Add-cover-image fallback ──
// Shown when a URL import succeeded (product was created) but the source
// couldn't be scraped for a usable image — a bot-protected storefront, a
// generic site with no og:image, etc. The product already has its real
// name/price/description; this only needs the image.
function AddCoverImageDialog({
  productId,
  productName,
  onClose,
  onUploaded,
}: {
  productId: string;
  productName: string;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const { toast } = useToast();
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!coverFile || saving) return;
    setSaving(true);
    try {
      await productsApi.uploadAsset(productId, coverFile, "cover");
      onUploaded();
    } catch (err: any) {
      Sentry.captureException(err);
      toast({
        title: "Couldn't upload image",
        description: err?.response?.data?.detail || err.message || "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold text-text">Add a cover image</h2>
        <p className="mt-1 text-sm text-text-muted">
          We couldn't automatically fetch an image for <span className="text-text">{productName}</span>.
          Upload one to finish setting it up.
        </p>
        <div className="mt-4">
          <Input
            type="file"
            accept="image/*"
            onChange={(e) => setCoverFile(e.target.files?.[0] ?? null)}
            data-testid="add-cover-image-input"
          />
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
            Skip for now
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!coverFile || saving} className="gap-2">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {saving ? "Uploading..." : "Add image"}
          </Button>
        </div>
      </div>
    </div>
  );
}


function SetPriceDialog({
  productId,
  productName,
  onClose,
  onSaved,
}: {
  productId: string;
  productName: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const [price, setPrice] = useState("");
  const [saving, setSaving] = useState(false);

  const parsed = parseFloat(price);
  const valid = !isNaN(parsed) && parsed > 0;

  const handleSave = async () => {
    if (!valid || saving) return;
    setSaving(true);
    try {
      await productsApi.update(productId, { price: parsed, current_price: parsed } as any);
      onSaved();
    } catch (err: any) {
      Sentry.captureException(err);
      toast({
        title: "Couldn't save price",
        description: err?.response?.data?.detail || err.message || "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold text-text">Set a price</h2>
        <p className="mt-1 text-sm text-text-muted">
          We couldn't automatically find a price for <span className="text-text">{productName}</span>
          {" "}(the source page may show tiered/negotiated pricing instead of one fixed price). Enter one to finish setting it up.
        </p>
        <div className="mt-4">
          <Input
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            data-testid="set-price-input"
          />
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
            Skip for now
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!valid || saving} className="gap-2">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {saving ? "Saving..." : "Save price"}
          </Button>
        </div>
      </div>
    </div>
  );
}


// ── Main Page ──

export function ProductLibraryPage() {
  // Discover panel removed: bulk Apify scrapes (trending / search / categories)
  // were costing ~$241/month. Product import is now URL-only via the
  // "Paste any product URL" input above the grid — see import_from_url in
  // backend/orchestrator/routers/products.py. The DiscoverTab component
  // below is preserved for the day we re-enable a cheaper alternative.
  const discoverOpen = false; // hard-off until cost-controlled discovery is back

  return (
    <div className="mx-auto max-w-[1400px] space-y-6 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text">My Products</h1>
      </div>

      {/* URL Import Bar — hero position */}
      <UrlImportBar />

      {/* My Products grid */}
      <MyLibraryTab />

      {/* Discovery disabled (cost) — surface the URL-import path instead. */}
      <div className="border-t border-border pt-4">
        <div className="flex items-start gap-3 p-4 bg-white/5 border border-white/10 rounded-xl">
          <Link className="w-5 h-5 mt-0.5 text-accent/70 shrink-0" />
          <div className="text-sm">
            <p className="font-medium text-text">Import a product from a URL</p>
            <p className="text-xs text-text-muted mt-1">
              Paste a TikTok Shop, Amazon, or any product page URL into the
              search bar at the top to import that product automatically.
            </p>
          </div>
        </div>
        {discoverOpen && (
          <div className="mt-4">
            <DiscoverTab />
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════
//  DISCOVER TAB
// ═══════════════════════════════════════════

function DiscoverTab() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // State
  const [section, setSection] = useState("top_selling");
  const [sortBy, setSortBy] = useState("revenue");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [isSearchMode, setIsSearchMode] = useState(false);
  const [importedIds, setImportedIds] = useState<Set<string>>(new Set());
  const [selectedDiscoverProduct, setSelectedDiscoverProduct] = useState<DiscoverProduct | null>(null);

  // Filters
  const [showFilters, setShowFilters] = useState(true);
  const [filterCategory, setFilterCategory] = useState("");
  const [filterMinPrice, setFilterMinPrice] = useState(0);
  const [filterMaxPrice, setFilterMaxPrice] = useState(0);
  const [filterMinRevenue, setFilterMinRevenue] = useState(0);
  const [filterMinItemsSold, setFilterMinItemsSold] = useState(0);
  const [filterRevenueGrowth, setFilterRevenueGrowth] = useState(0);
  const [filterMinCommission, setFilterMinCommission] = useState(0);
  const [filterMinRating, setFilterMinRating] = useState(0);
  const [filterAffiliateOnly, setFilterAffiliateOnly] = useState(false);

  const perPage = 30;

  // Fetch trending with infinite scroll
  const {
    data: trendingInf,
    isLoading: trendingLoading,
    fetchNextPage: fetchNextTrending,
    hasNextPage: hasNextTrending,
    isFetchingNextPage: isFetchingNextTrending,
  } = useInfiniteQuery({
    queryKey: ["discover-trending", section, sortBy],
    queryFn: ({ pageParam = 1 }) => discoverApi.trending({ section, page: pageParam, per_page: perPage, sort_by: sortBy }),
    getNextPageParam: (lastPage: any, pages: any[]) => lastPage.has_more ? pages.length + 1 : undefined,
    initialPageParam: 1,
    enabled: !isSearchMode,
  });

  // Fetch search with infinite scroll
  const {
    data: searchInf,
    isLoading: searchLoading,
    fetchNextPage: fetchNextSearch,
    hasNextPage: hasNextSearch,
    isFetchingNextPage: isFetchingNextSearch,
  } = useInfiniteQuery({
    queryKey: ["discover-search", searchQuery, filterCategory, filterMinPrice, filterMaxPrice, filterMinRevenue, filterMinItemsSold, filterRevenueGrowth, filterMinCommission, filterMinRating, filterAffiliateOnly, sortBy],
    queryFn: ({ pageParam = 1 }) =>
      discoverApi.search({
        q: searchQuery,
        category: filterCategory,
        min_price: filterMinPrice,
        max_price: filterMaxPrice,
        min_revenue: filterMinRevenue,
        min_items_sold: filterMinItemsSold,
        revenue_growth_min: filterRevenueGrowth,
        min_commission: filterMinCommission,
        min_rating: filterMinRating,
        is_affiliate: filterAffiliateOnly || undefined,
        sort_by: sortBy,
        page: pageParam,
        per_page: perPage,
      }),
    getNextPageParam: (lastPage: any, pages: any[]) => lastPage.has_more ? pages.length + 1 : undefined,
    initialPageParam: 1,
    enabled: isSearchMode,
  });

  // Categories
  const { data: catData } = useQuery({
    queryKey: ["discover-categories"],
    queryFn: () => discoverApi.categories(),
  });
  const categories = catData?.categories || [];

  const products = isSearchMode
    ? searchInf?.pages.flatMap((p: any) => p.products) ?? []
    : trendingInf?.pages.flatMap((p: any) => p.products) ?? [];
  const total = isSearchMode
    ? searchInf?.pages[0]?.total ?? 0
    : trendingInf?.pages[0]?.total ?? 0;
  const isLoading = isSearchMode ? searchLoading : trendingLoading;
  const hasNextPage = isSearchMode ? hasNextSearch : hasNextTrending;
  const isFetchingNextPage = isSearchMode ? isFetchingNextSearch : isFetchingNextTrending;
  const fetchNextPage = isSearchMode ? fetchNextSearch : fetchNextTrending;
  const source = isSearchMode
    ? searchInf?.pages[0]?.source
    : trendingInf?.pages[0]?.source;

  // IntersectionObserver sentinel for infinite scroll
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!sentinelRef.current || !hasNextPage) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting && !isFetchingNextPage) fetchNextPage(); },
      { rootMargin: "400px" },
    );
    obs.observe(sentinelRef.current);
    return () => obs.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const handleSearch = () => {
    if (searchInput.trim()) {
      setSearchQuery(searchInput.trim());
      setIsSearchMode(true);
    }
  };

  const clearSearch = () => {
    setSearchInput("");
    setSearchQuery("");
    setIsSearchMode(false);
  };

  const resetFilters = () => {
    setFilterCategory("");
    setFilterMinPrice(0);
    setFilterMaxPrice(0);
    setFilterMinRevenue(0);
    setFilterMinItemsSold(0);
    setFilterRevenueGrowth(0);
    setFilterMinCommission(0);
    setFilterMinRating(0);
    setFilterAffiliateOnly(false);
  };

  const applyFilters = () => {
    setIsSearchMode(true);
  };

  const importMutation = useMutation({
    mutationFn: (tpId: string) => discoverApi.importProduct(tpId),
    onSuccess: (data: any, tpId: string) => {
      const product = products.find((p) => p.id === tpId);
      setImportedIds((prev) => new Set(prev).add(product?.tiktok_product_id || tpId));
      queryClient.invalidateQueries({ queryKey: ["products"] });
      if (data.already_imported) {
        toast({ title: "Already in your library" });
      } else {
        const s = data.import_summary;
        const parts = [];
        if (s?.additional_images > 0) parts.push(`${s.additional_images} images`);
        if (s?.videos > 0) parts.push(`${s.videos} video${s.videos > 1 ? "s" : ""}`);
        if (s?.variants > 0) parts.push(`${s.variants} variants`);
        const detail = parts.length > 0 ? ` · ${parts.join(", ")}` : "";
        toast({ title: `Added to library${detail}` });
      }
    },
    onError: () => toast({ title: "Import failed", variant: "destructive" }),
  });

  const handleSort = (col: string) => {
    setSortBy(col);
  };

  const SortArrow = ({ col }: { col: string }) =>
    sortBy === col ? <span className="ml-1 text-accent">&#9660;</span> : null;

  return (
    <div className="flex gap-4">
      {/* ── Left Sidebar Filters ── */}
      {showFilters && (
        <div className="w-64 shrink-0 space-y-4 rounded-lg border border-border bg-card p-4 self-start sticky top-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-text">Filters</h3>
            <button onClick={() => setShowFilters(false)} className="text-text-muted hover:text-text">
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Category */}
          <FilterGroup title="Category">
            <CategoryPicker
              categories={categories}
              selected={filterCategory}
              onSelect={(c) => setFilterCategory(c)}
            />
          </FilterGroup>

          {/* Revenue */}
          <FilterGroup title="Revenue ($)">
            <RadioGroup
              options={[
                { label: "All", value: 0 },
                { label: "< $100", value: -100 },
                { label: "$100 - $1k", value: 100 },
                { label: "$1k - $10k", value: 1000 },
                { label: "> $10k", value: 10000 },
              ]}
              value={filterMinRevenue}
              onChange={setFilterMinRevenue}
            />
          </FilterGroup>

          {/* Items Sold */}
          <FilterGroup title="Items Sold">
            <RadioGroup
              options={[
                { label: "All", value: 0 },
                { label: "> 10", value: 10 },
                { label: "> 100", value: 100 },
                { label: "> 1k", value: 1000 },
                { label: "> 10k", value: 10000 },
              ]}
              value={filterMinItemsSold}
              onChange={setFilterMinItemsSold}
            />
          </FilterGroup>

          {/* Revenue Growth */}
          <FilterGroup title="Revenue Growth Rate">
            <RadioGroup
              options={[
                { label: "All", value: 0 },
                { label: "> 0%", value: 0.1 },
                { label: "> 30%", value: 30 },
                { label: "> 70%", value: 70 },
                { label: "> 100%", value: 100 },
              ]}
              value={filterRevenueGrowth}
              onChange={setFilterRevenueGrowth}
            />
          </FilterGroup>

          {/* Price */}
          <FilterGroup title="Avg. Unit Price ($)">
            <RadioGroup
              options={[
                { label: "All", value: 0 },
                { label: "< $5", value: -5 },
                { label: "$5 - $20", value: 5 },
                { label: "$20 - $100", value: 20 },
                { label: "> $100", value: 100 },
              ]}
              value={filterMinPrice}
              onChange={(v) => {
                if (v < 0) {
                  setFilterMinPrice(0);
                  setFilterMaxPrice(Math.abs(v));
                } else if (v === 5) {
                  setFilterMinPrice(5);
                  setFilterMaxPrice(20);
                } else if (v === 20) {
                  setFilterMinPrice(20);
                  setFilterMaxPrice(100);
                } else if (v >= 100) {
                  setFilterMinPrice(100);
                  setFilterMaxPrice(0);
                } else {
                  setFilterMinPrice(0);
                  setFilterMaxPrice(0);
                }
              }}
            />
          </FilterGroup>

          {/* Commission */}
          <FilterGroup title="Commission Rate">
            <RadioGroup
              options={[
                { label: "All", value: 0 },
                { label: "> 5%", value: 0.05 },
                { label: "> 10%", value: 0.1 },
                { label: "> 15%", value: 0.15 },
                { label: "> 20%", value: 0.2 },
              ]}
              value={filterMinCommission}
              onChange={setFilterMinCommission}
            />
          </FilterGroup>

          {/* Rating */}
          <FilterGroup title="Min Rating">
            <RadioGroup
              options={[
                { label: "All", value: 0 },
                { label: "> 3", value: 3 },
                { label: "> 4", value: 4 },
                { label: "> 4.5", value: 4.5 },
              ]}
              value={filterMinRating}
              onChange={setFilterMinRating}
            />
          </FilterGroup>

          {/* Affiliate Only */}
          <label className="flex items-center gap-2 text-sm text-text cursor-pointer">
            <input
              type="checkbox"
              checked={filterAffiliateOnly}
              onChange={(e) => setFilterAffiliateOnly(e.target.checked)}
              className="rounded border-border"
            />
            Affiliate Only
          </label>

          {/* Action buttons */}
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={resetFilters} className="flex-1 gap-1 text-xs">
              <RotateCcw className="h-3 w-3" /> Reset
            </Button>
            <Button size="sm" onClick={applyFilters} className="flex-1 text-xs">
              Apply Filters
            </Button>
          </div>
        </div>
      )}

      {/* ── Main Content ── */}
      <div className="flex-1 min-w-0 space-y-4">
        {/* Section Tabs */}
        <div className="flex items-center gap-1 overflow-x-auto">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.key}
                onClick={() => { setSection(s.key); setIsSearchMode(false); setSearchInput(""); setSearchQuery(""); }}
                className={`flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  section === s.key && !isSearchMode
                    ? "bg-accent/15 text-accent"
                    : "text-text-dim hover:bg-surface hover:text-text"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {s.label}
              </button>
            );
          })}
        </div>

        {/* Search Bar + Filter Toggle */}
        <div className="flex items-center gap-2">
          {!showFilters && (
            <Button size="sm" variant="outline" onClick={() => setShowFilters(true)} className="gap-1">
              <Filter className="h-3.5 w-3.5" /> Filters
            </Button>
          )}
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="Search product name..."
              className="pl-10"
            />
          </div>
          <Button onClick={handleSearch} size="sm">Search</Button>
          {isSearchMode && (
            <Button onClick={clearSearch} size="sm" variant="ghost">Clear</Button>
          )}
        </div>

        {/* Active Filter Chips */}
        {(filterCategory || filterAffiliateOnly) && (
          <div className="flex flex-wrap gap-2">
            {filterCategory && (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent">
                {filterCategory}
                <button onClick={() => setFilterCategory("")}><X className="h-3 w-3" /></button>
              </span>
            )}
            {filterAffiliateOnly && (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent">
                Affiliate Only
                <button onClick={() => setFilterAffiliateOnly(false)}><X className="h-3 w-3" /></button>
              </span>
            )}
          </div>
        )}

        {/* Data Table */}
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border bg-surface text-xs text-text-dim">
                <th className="px-3 py-2 w-10">#</th>
                <th className="px-3 py-2 w-[280px]">Product Info</th>
                <th className="px-3 py-2 w-[100px] cursor-pointer hover:text-text" onClick={() => handleSort("revenue")}>
                  Revenue<SortArrow col="revenue" />
                </th>
                <th className="px-3 py-2 w-[80px] cursor-pointer hover:text-text" onClick={() => handleSort("growth")}>
                  Growth<SortArrow col="growth" />
                </th>
                <th className="px-3 py-2 w-[90px] cursor-pointer hover:text-text" onClick={() => handleSort("items_sold")}>
                  Items Sold<SortArrow col="items_sold" />
                </th>
                <th className="px-3 py-2 w-[80px] cursor-pointer hover:text-text" onClick={() => handleSort("price")}>
                  Avg Price<SortArrow col="price" />
                </th>
                <th className="px-3 py-2 w-[90px] cursor-pointer hover:text-text" onClick={() => handleSort("commission")}>
                  Commission<SortArrow col="commission" />
                </th>
                <th className="px-3 py-2 w-[60px]"></th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-border">
                    <td colSpan={9} className="px-3 py-4">
                      <div className="h-8 animate-pulse rounded bg-surface" />
                    </td>
                  </tr>
                ))
              ) : products.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-3 py-12 text-center">
                    <Package className="mx-auto mb-3 h-10 w-10 text-text-muted" />
                    <p className="text-sm text-text-dim">
                      {isSearchMode ? "No products match your search" : "Loading trending products..."}
                    </p>
                  </td>
                </tr>
              ) : (
                products.map((product, idx) => (
                  <tr
                    key={product.id}
                    className="border-b border-border hover:bg-surface/50 transition-colors cursor-pointer"
                    onClick={() => setSelectedDiscoverProduct(product)}
                  >
                    <td className="px-3 py-2.5 text-xs text-text-muted">
                      {idx + 1}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-3">
                        <div className="relative shrink-0">
                          <DiscoverTileImage url={product.cover_image_url} name={product.title} className="h-12 w-12 rounded-md" />
                          {/* TikTok icon */}
                          <svg
                            viewBox="0 0 24 24"
                            className="absolute -top-1 -left-1 w-4 h-4 text-text-dim bg-card rounded-full p-0.5"
                            fill="currentColor"
                          >
                            <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.12v-3.49a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.16 15.8a6.34 6.34 0 0010.86 4.47V13.4a8.28 8.28 0 005.57 2.14v-3.44a4.85 4.85 0 01-3.57-1.98V6.69h3.57z" />
                          </svg>
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-text truncate max-w-[200px]">{product.title}</p>
                          <p className="text-xs text-text-muted">
                            ${product.current_price.toFixed(2)}
                            {product.original_price > product.current_price && (
                              <span className="ml-1 line-through text-text-muted/50">
                                ${product.original_price.toFixed(2)}
                              </span>
                            )}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-sm font-medium text-text">
                      {formatRevenue(product.revenue_cents)}
                    </td>
                    <td className="px-3 py-2.5">

                    </td>
                    <td className="px-3 py-2.5">
                      {product.revenue_growth_rate !== 0 ? (
                        <span
                          className={`text-sm font-medium ${
                            product.revenue_growth_rate > 0 ? "text-green-500" : "text-red-500"
                          }`}
                        >
                          {product.revenue_growth_rate > 0 ? "+" : ""}
                          {product.revenue_growth_rate.toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-xs text-text-muted">&mdash;</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-sm text-text">
                      {product.items_sold ? formatNumber(product.items_sold) : "\u2014"}
                    </td>
                    <td className="px-3 py-2.5 text-sm text-text">
                      ${product.avg_unit_price.toFixed(2)}
                    </td>
                    <td className="px-3 py-2.5 text-sm text-text">
                      {product.commission_display || "\u2014"}
                    </td>
                    <td className="px-3 py-2.5">
                      <Button
                        size="sm"
                        variant={importedIds.has(product.tiktok_product_id) ? "ghost" : "outline"}
                        disabled={importMutation.isPending && importMutation.variables === product.id}
                        onClick={(e) => { e.stopPropagation(); importMutation.mutate(product.id); }}
                        className="text-xs h-7 px-2"
                      >
                        {importedIds.has(product.tiktok_product_id) ? "Added" : "+ Add"}
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Infinite scroll sentinel + loading indicator */}
        <div ref={sentinelRef} className="h-4" />
        {isFetchingNextPage && (
          <div className="flex justify-center py-4">
            <Loader2 className="h-6 w-6 animate-spin text-accent" />
          </div>
        )}
        {!hasNextPage && products.length > 0 && (
          <div className="text-center py-3">
            <span className="text-xs text-text-muted">{products.length} of {total} products shown</span>
          </div>
        )}
        {!isLoading && products.length === 0 && (
          <div className="rounded-lg border border-border bg-surface p-6 text-center">
            <p className="text-sm text-text-muted">No products found.</p>
            <p className="text-xs text-text-muted mt-1">Try a different section or adjust filters.</p>
          </div>
        )}
      </div>

      {/* ── Discover Product Preview Slide-Over ── */}
      {selectedDiscoverProduct && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/30" onClick={() => setSelectedDiscoverProduct(null)} />
          <div className="relative w-[420px] bg-card border-l border-border overflow-y-auto shadow-xl">
            <div className="sticky top-0 bg-card border-b border-border px-4 py-3 flex items-center justify-between z-10">
              <button onClick={() => setSelectedDiscoverProduct(null)} className="text-sm text-text-dim hover:text-text">
                ← Back
              </button>
              <Button
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  importMutation.mutate(selectedDiscoverProduct.id);
                  setSelectedDiscoverProduct(null);
                }}
                disabled={importedIds.has(selectedDiscoverProduct.tiktok_product_id)}
              >
                {importedIds.has(selectedDiscoverProduct.tiktok_product_id) ? "✓ In Library" : "+ Add to Library"}
              </Button>
            </div>

            <div className="p-4 space-y-4">
              {/* Cover image (large) */}
              <DiscoverTileImage url={selectedDiscoverProduct.cover_image_url} name={selectedDiscoverProduct.title} className="w-full aspect-square rounded-lg" />

              {/* Title + Price */}
              <h2 className="text-lg font-semibold text-text">{selectedDiscoverProduct.title}</h2>
              <div className="flex items-center gap-2">
                <span className="text-xl font-bold text-text">
                  ${selectedDiscoverProduct.current_price.toFixed(2)}
                </span>
                {selectedDiscoverProduct.original_price > selectedDiscoverProduct.current_price && (
                  <span className="text-sm text-text-muted line-through">
                    ${selectedDiscoverProduct.original_price.toFixed(2)}
                  </span>
                )}
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg bg-surface p-3">
                  <p className="text-xs text-text-muted">Revenue</p>
                  <p className="text-sm font-bold text-text">{selectedDiscoverProduct.revenue_display || "—"}</p>
                </div>
                <div className="rounded-lg bg-surface p-3">
                  <p className="text-xs text-text-muted">Items Sold</p>
                  <p className="text-sm font-bold text-text">{selectedDiscoverProduct.items_sold ? formatNumber(selectedDiscoverProduct.items_sold) : "—"}</p>
                </div>
                <div className="rounded-lg bg-surface p-3">
                  <p className="text-xs text-text-muted">Growth</p>
                  <p className={`text-sm font-bold ${selectedDiscoverProduct.revenue_growth_rate > 0 ? "text-green-500" : "text-red-500"}`}>
                    {selectedDiscoverProduct.revenue_growth_rate ? `${selectedDiscoverProduct.revenue_growth_rate > 0 ? "+" : ""}${selectedDiscoverProduct.revenue_growth_rate.toFixed(1)}%` : "—"}
                  </p>
                </div>
                <div className="rounded-lg bg-surface p-3">
                  <p className="text-xs text-text-muted">Commission</p>
                  <p className="text-sm font-bold text-text">{selectedDiscoverProduct.commission_display || "—"}</p>
                </div>
              </div>

              {/* Category + Seller */}
              {selectedDiscoverProduct.category && (
                <p className="text-xs text-text-muted">Category: {selectedDiscoverProduct.category}</p>
              )}
              {selectedDiscoverProduct.seller_name && (
                <p className="text-xs text-text-muted">Seller: {selectedDiscoverProduct.seller_name}</p>
              )}

              {/* TikTok link */}
              {selectedDiscoverProduct.product_url && (
                <a
                  href={selectedDiscoverProduct.product_url}
                  target="_blank"
                  rel="noopener"
                  className="block text-center text-sm text-accent hover:underline mt-4"
                >
                  View on TikTok Shop ↗
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Filter Components ──

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between text-xs font-medium text-text-dim"
      >
        {title}
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

function RadioGroup({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: number }[];
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => (
        <button
          key={opt.label}
          onClick={() => onChange(opt.value === value ? 0 : opt.value)}
          className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
            value === opt.value && opt.value !== 0
              ? "bg-accent/15 text-accent font-medium"
              : opt.value === 0 && value === 0
              ? "bg-accent/15 text-accent font-medium"
              : "bg-surface text-text-dim hover:text-text"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function CategoryPicker({
  categories,
  selected,
  onSelect,
}: {
  categories: CategoryTree[];
  selected: string;
  onSelect: (cat: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [hoveredParent, setHoveredParent] = useState<string | null>(null);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full text-left rounded border border-border bg-surface px-2 py-1.5 text-xs text-text-dim hover:text-text"
      >
        {selected || "All categories"}
        <ChevronDown className="inline ml-1 h-3 w-3" />
      </button>
    );
  }

  const hovered = categories.find((c) => c.name === hoveredParent);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(false)}
        className="w-full text-left rounded border border-accent bg-surface px-2 py-1.5 text-xs text-accent"
      >
        {selected || "All categories"}
        <ChevronUp className="inline ml-1 h-3 w-3" />
      </button>
      <div className="absolute z-20 mt-1 left-0 flex rounded-lg border border-border bg-card shadow-lg">
        {/* Parent categories */}
        <div className="w-48 max-h-64 overflow-y-auto border-r border-border p-1">
          <button
            onClick={() => { onSelect(""); setOpen(false); }}
            className="w-full text-left rounded px-2 py-1 text-xs text-text-dim hover:bg-surface"
          >
            All categories
          </button>
          {categories.map((cat) => (
            <button
              key={cat.name}
              onMouseEnter={() => setHoveredParent(cat.name)}
              onClick={() => { onSelect(cat.name); setOpen(false); }}
              className={`w-full text-left rounded px-2 py-1 text-xs transition-colors ${
                selected === cat.name
                  ? "bg-accent/15 text-accent"
                  : "text-text hover:bg-surface"
              }`}
            >
              {cat.name}
            </button>
          ))}
        </div>
        {/* Subcategories */}
        {hovered && (
          <div className="w-40 max-h-64 overflow-y-auto p-1">
            {hovered.subcategories.map((sub) => (
              <button
                key={sub}
                onClick={() => { onSelect(sub); setOpen(false); }}
                className={`w-full text-left rounded px-2 py-1 text-xs transition-colors ${
                  selected === sub
                    ? "bg-accent/15 text-accent"
                    : "text-text hover:bg-surface"
                }`}
              >
                {sub}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════
//  MY LIBRARY TAB
// ═══════════════════════════════════════════

function MyLibraryTab() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [sort, setSort] = useState("newest");
  const [filter, setFilter] = useState("all");
  const [selectedProductId, setSelectedProductId] = useState<string | null>(null);

  const doSearch = () => {
    setSearch(searchInput);
    setPage(1);
  };

  // Library-only search: My Products' search input must NEVER call external
  // sources (TikTok / Amazon / Apify discover). productsApi.list hits
  // GET /api/products?search=… which filters by name/description/category
  // on the user's already-imported library.
  const { data, isLoading } = useQuery({
    queryKey: ["products", page, search, sort, filter],
    queryFn: () => productsApi.list({ page, per_page: 30, search, sort, filter }),
  });

  const products = data?.products || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / 16);

  // Prefetch next page
  useQuery({
    queryKey: ["products", page + 1, search, sort, filter],
    queryFn: () => productsApi.list({ page: page + 1, per_page: 30, search, sort, filter }),
    enabled: page < totalPages,
  });

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
            placeholder="Search products..."
            className="pl-10"
          />
        </div>
        <Button variant="outline" onClick={doSearch} className="gap-2" size="sm">
          <Search className="h-4 w-4" /> Search
        </Button>
        <select
          value={filter}
          onChange={(e) => { setFilter(e.target.value); setPage(1); }}
          className="h-9 rounded-md border border-border bg-surface px-3 text-sm text-text"
        >
          <option value="all">All</option>
          <option value="with_video">With video</option>
          <option value="missing_assets">Missing assets</option>
        </select>
        <select
          value={sort}
          onChange={(e) => { setSort(e.target.value); setPage(1); }}
          className="h-9 rounded-md border border-border bg-surface px-3 text-sm text-text"
        >
          <option value="newest">Newest</option>
          <option value="created_asc">Oldest</option>
          <option value="commission_desc">Highest commission %</option>
          <option value="commission_asc">Lowest commission %</option>
          <option value="commission_amount_desc">Highest commission $</option>
          <option value="price_asc">Price ascending</option>
          <option value="price_desc">Price descending</option>
          <option value="best_selling">Best selling</option>
          <option value="highest_rated">Highest rated</option>
          <option value="name_asc">Name A–Z</option>
        </select>
      </div>

      {/* Product Grid */}
      {isLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-64 animate-pulse rounded-lg bg-card" />
          ))}
        </div>
      ) : products.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card p-12 text-center">
          <Link className="mx-auto mb-4 h-12 w-12 text-accent/60" />
          <h3 className="text-lg font-semibold text-text">Import your first product</h3>
          <p className="mt-1 text-sm text-text-dim">Paste any product URL above to get started</p>
          <p className="mt-0.5 text-xs text-text-muted">Supports TikTok Shop, Amazon, Shopify, and more</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {products.map((p) => (
            <RichProductCard
              key={p.id}
              product={p}
              onClick={() => setSelectedProductId(p.id)}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-4">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm text-text-dim">Page {page} of {totalPages}</span>
          <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Product Detail Slide-Over */}
      {selectedProductId && (
        <ProductDetailPanel
          productId={selectedProductId}
          onClose={() => setSelectedProductId(null)}
        />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
//  RICH PRODUCT CARD (My Library)
// ═══════════════════════════════════════════

function RichProductCard({ product: p, onClick }: {
  product: ProductWithAssets;
  onClick: () => void;
}) {
  const coverUrl = p.cover_image_url || "";
  const currentPrice = p.current_price ?? 0;
  const originalPrice = p.original_price ?? 0;
  const price = p.price ?? 0;
  const hasDiscount = originalPrice > 0 && originalPrice > (currentPrice || price) && (currentPrice || price) > 0;
  const displayPrice = currentPrice > 0 ? currentPrice : price;

  return (
    <div
      className="rounded-lg border border-border bg-card overflow-hidden cursor-pointer hover:border-accent/30 hover:shadow-md transition-all"
      onClick={(e) => { e.stopPropagation(); onClick(); }}
    >
      {/* Cover Image */}
      <div className="relative aspect-square bg-surface">
        {coverUrl ? (
          <img
            src={coverUrl}
            alt={p.name}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="h-full w-full flex items-center justify-center">
            <Package className="h-10 w-10 text-text-muted" />
          </div>
        )}
        <SourceBadge
          commissionSource={(p as any).commission_source}
          url={(p as any).product_url || p.tiktok_product_url}
        />
        {/* Commission badge wins the top-right slot when present; the
            discount tag falls back to the bottom-right when both apply
            so neither badge is hidden behind the other. */}
        {p.commission_rate != null && p.commission_rate > 0 ? (
          <CommissionBadge
            commissionRate={p.commission_rate}
            price={p.price}
            commissionSource={(p as any).commission_source}
          />
        ) : null}
        {hasDiscount && (p.discount_percent ?? 0) > 0 && (
          <span
            className={cn(
              "absolute rounded bg-danger px-1.5 py-0.5 text-[10px] font-bold text-white",
              p.commission_rate != null && p.commission_rate > 0 ? "bottom-2 right-2" : "top-2 right-2",
            )}
          >
            -{Math.round(p.discount_percent ?? 0)}%
          </span>
        )}
      </div>

      {/* Info */}
      <div className="p-3 space-y-1.5">
        {/* Title */}
        <h3 className="text-sm font-medium text-text line-clamp-2 leading-tight min-h-[2.5em]">
          {p.name}
        </h3>

        {/* Price */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-sm font-bold text-text">${displayPrice.toFixed(2)}</span>
          {hasDiscount && (
            <span className="text-xs text-text-muted line-through">
              ${originalPrice.toFixed(2)}
            </span>
          )}
        </div>

        {/* Rating + Reviews */}
        {(p.rating ?? 0) > 0 && (
          <div className="flex items-center gap-1 text-xs text-text-dim">
            <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
            <span>{(p.rating ?? 0).toFixed(1)}</span>
            {(p.review_count ?? 0) > 0 && (
              <span className="text-text-muted">({formatNumber(p.review_count ?? 0)})</span>
            )}
          </div>
        )}

        {/* Items Sold */}
        {(p.sales_volume ?? 0) > 0 && (
          <p className="text-xs text-text-muted">{formatNumber(p.sales_volume ?? 0)} sold</p>
        )}

        {/* Category + Seller */}
        <div className="space-y-0.5">
          {p.category && (
            <p className="text-[11px] text-text-muted truncate">{p.category}</p>
          )}
          {p.seller_name && (
            <p className="text-[11px] text-text-muted truncate">Seller: {p.seller_name}</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════
//  PRODUCT DETAIL SLIDE-OVER PANEL
// ═══════════════════════════════════════════

function ProductDetailPanel({ productId, onClose }: {
  productId: string;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: product, isLoading } = useQuery({
    queryKey: ["product-detail", productId],
    queryFn: () => productsApi.get(productId),
  });

  const deleteMutation = useMutation({
    mutationFn: () => productsApi.delete(productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      toast({ title: "Product deleted" });
      onClose();
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.detail || "Delete failed";
      toast({ title: msg, variant: "destructive" });
    },
  });

  const uploadAssetMutation = useMutation({
    mutationFn: ({ file, assetType }: { file: File; assetType: string }) =>
      productsApi.uploadAsset(productId, file, assetType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      toast({ title: "Asset uploaded" });
    },
    onError: () => toast({ title: "Upload failed", variant: "destructive" }),
  });

  const deleteAssetMutation = useMutation({
    mutationFn: (assetId: string) => productsApi.deleteAsset(productId, assetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      toast({ title: "Asset deleted" });
    },
  });

  // Re-run the URL resolver and backfill any blank fields on the row.
  // Useful for legacy products that were imported when the resolver had
  // a schema mismatch with the data source (e.g. the early Amazon imports
  // that ended up with no cover image / $0 price).
  const refreshMutation = useMutation({
    mutationFn: () => productsApi.refresh(productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      toast({ title: "Product data refreshed", variant: "success" });
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.detail || "Refresh failed";
      toast({ title: msg, variant: "destructive" });
    },
  });

  const handleUploadCover = (file: File | undefined) => {
    if (file) uploadAssetMutation.mutate({ file, assetType: "cover" });
  };

  const handleUploadImages = (files: FileList | null) => {
    if (files) {
      Array.from(files).forEach((file) =>
        uploadAssetMutation.mutate({ file, assetType: "product_shot" })
      );
    }
  };

  const handleUploadVideo = (file: File | undefined) => {
    if (file) uploadAssetMutation.mutate({ file, assetType: "demo" });
  };

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const [customPrompt, setCustomPrompt] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [videoStyle, setVideoStyle] = useState<string>("product_showcase");
  const [videoDuration, setVideoDuration] = useState<5 | 10>(5);
  const [videoQuality, setVideoQuality] = useState<"pro" | "fast">("pro");
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!lightboxUrl) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightboxUrl(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [lightboxUrl]);

  // Manual commission entry (FEATURE 3.3) — only used when
  // commission_source === "manual". The percentage is entered as 0-100
  // and converted to a fraction (0-1) before hitting the backend.
  const [showCommissionEditor, setShowCommissionEditor] = useState(false);
  const [commissionInput, setCommissionInput] = useState("");

  // Auth user (for the "connect Amazon Associates" amber callout — we
  // need to read amazon_associate_tag, which lives on the persisted
  // auth store user record).
  const authUser = useAuthStore((s) => s.user);
  const navigate = useNavigate();

  const updateProductMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => productsApi.update(productId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setShowCommissionEditor(false);
      toast({ title: "Commission updated" });
    },
    onError: (err: any) => {
      toast({
        title: "Could not update commission",
        description: err?.response?.data?.detail || err?.message || "Try again",
        variant: "destructive",
      });
    },
  });

  const saveManualCommission = () => {
    const raw = commissionInput.trim();
    if (raw === "") return;
    const pct = Number(raw);
    // Client-side validation per spec line 376 (0-100 inclusive). The
    // backend column is unconstrained, so this is the only guard.
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      toast({
        title: "Enter a number between 0 and 100",
        variant: "destructive",
      });
      return;
    }
    updateProductMutation.mutate({ commission_rate: pct / 100 });
  };

  const generateImageMutation = useMutation({
    mutationFn: (style: string) =>
      productsApi.generateAiImages(productId, style !== "custom" ? style : "product_shot", style === "custom" ? customPrompt : ""),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      toast({ title: "AI image generated!" });
    },
    onError: (err: any) => {
      toast({ title: "Generation failed", description: err?.response?.data?.detail || "Check FAL_API_KEY is configured", variant: "destructive" });
    },
  });

  const generateVideoMutation = useMutation({
    mutationFn: ({ style, customPrompt: cp }: { style: string; customPrompt: string }) =>
      productsApi.generateAiVideo(productId, style, videoDuration, cp, videoQuality),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      toast({ title: "Video generated!" });
    },
    onError: (err: any) => {
      toast({ title: "Video generation failed", description: err?.response?.data?.detail, variant: "destructive" });
    },
  });

  const removeBackgroundMutation = useMutation({
    mutationFn: () => productsApi.removeBackground(productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-detail", productId] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      toast({ title: "Background removed!" });
    },
    onError: (err: any) => {
      toast({ title: "Background removal failed", description: err?.response?.data?.detail, variant: "destructive" });
    },
  });

  const p = product as ProductDetailResponse | undefined;
  const pCurrentPrice = p?.current_price ?? 0;
  const pOriginalPrice = p?.original_price ?? 0;
  const pPrice = p?.price ?? 0;
  const hasDiscount = p && pOriginalPrice > 0 && pOriginalPrice > (pCurrentPrice || pPrice);
  const displayPrice = p ? (pCurrentPrice || pPrice) : 0;
  const coverUrl = p?.cover_image_url || "";
  // Inject the creator's connected Amazon Associates tag so this link is a
  // real, attributed affiliate link — a bare Amazon URL earns no commission
  // no matter how accurate the on-screen commission math is.
  const productUrl = buildOutboundProductUrl(
    p?.product_url || p?.tiktok_product_url || "",
    authUser?.amazon_associate_tag,
  );


  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/50" onClick={onClose} />

      {/* Slide-over panel */}
      <div className="fixed inset-y-0 right-0 z-50 w-full max-w-md bg-card border-l border-border shadow-xl overflow-y-auto animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between bg-card/95 backdrop-blur-xs border-b border-border px-4 py-3">
          <button onClick={onClose} className="flex items-center gap-1 text-sm text-text-dim hover:text-text">
            <ChevronLeft className="h-4 w-4" /> Back
          </button>
        </div>

        {isLoading || !p ? (
          <div className="p-6 space-y-4">
            <div className="aspect-square animate-pulse rounded-lg bg-surface" />
            <div className="h-6 w-3/4 animate-pulse rounded bg-surface" />
            <div className="h-4 w-1/2 animate-pulse rounded bg-surface" />
          </div>
        ) : (
          <div className="p-4 space-y-5">
            {/* Image Gallery — 3-column grid of all media */}
            <ProductMediaGalleryGrid product={p} />

            {/* Title */}
            <h2 className="text-lg font-semibold text-text leading-snug">{p.name}</h2>

            {/* Source label — keeps the detail view in sync with the
                top-left card badge so users always know where the
                product came from. */}
            {(() => {
              const label = sourceLabel(
                (p as any).commission_source,
                p.product_url || p.tiktok_product_url,
              );
              if (!label) return null;
              return (
                <div className="-mt-2 text-xs text-text-muted">
                  Source: <span className="text-text-dim font-medium">{label}</span>
                </div>
              );
            })()}

            {/* Price */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xl font-bold text-text">${displayPrice.toFixed(2)}</span>
              {hasDiscount && (
                <>
                  <span className="text-sm text-text-muted line-through">
                    ${pOriginalPrice.toFixed(2)}
                  </span>
                  {(p?.discount_percent ?? 0) > 0 && (
                    <Badge variant="danger" className="text-[11px]">
                      -{Math.round(p?.discount_percent ?? 0)}% OFF
                    </Badge>
                  )}
                </>
              )}
            </div>

            {/* Metrics strip */}
            <div className="grid grid-cols-3 gap-2">
              {(p?.rating ?? 0) > 0 && (
                <div className="rounded-lg bg-surface p-2.5">
                  <p className="text-[10px] text-text-muted">Rating</p>
                  <p className="text-sm font-bold text-text flex items-center gap-1">
                    <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                    {(p?.rating ?? 0).toFixed(1)}
                  </p>
                </div>
              )}
              {(p?.review_count ?? 0) > 0 && (
                <div className="rounded-lg bg-surface p-2.5">
                  <p className="text-[10px] text-text-muted">Reviews</p>
                  <p className="text-sm font-bold text-text">{formatNumber(p?.review_count ?? 0)}</p>
                </div>
              )}
              {(p?.sales_volume ?? 0) > 0 && (
                <div className="rounded-lg bg-surface p-2.5">
                  <p className="text-[10px] text-text-muted">Total Sold</p>
                  <p className="text-sm font-bold text-text">{formatNumber(p?.sales_volume ?? 0)}</p>
                </div>
              )}
              {(p?.sold_last_30_days ?? 0) > 0 && (
                <div className="rounded-lg bg-surface p-2.5">
                  <p className="text-[10px] text-text-muted">Sold (30d)</p>
                  <p className="text-sm font-bold text-text">{formatNumber(p?.sold_last_30_days ?? 0)}</p>
                </div>
              )}
              {(p?.commission_rate ?? 0) > 0 && (
                <div className="rounded-lg bg-surface p-2.5">
                  <p className="text-[10px] text-text-muted">Commission</p>
                  <p className="text-sm font-bold text-success">{((p?.commission_rate ?? 0) * 100).toFixed(0)}%</p>
                </div>
              )}
            </div>

            {/* Amazon Associates connect callout (FEATURE 3.2). Only
                shown when this is an Amazon product AND the user has
                NOT yet connected their associate tag AND a real
                commission rate is on the row (commission_rate=null
                signals a broken state, not an unconfigured one). */}
            {(p as any).commission_source === "amazon_associates"
              && !authUser?.amazon_associate_tag
              && (p.commission_rate ?? 0) > 0 && (
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-3">
                <div className="mb-1.5 flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 text-amber-400" />
                  <span className="text-sm font-medium text-amber-300">
                    Set up Amazon Associates to earn
                  </span>
                </div>
                <p className="text-xs text-text-muted leading-relaxed">
                  This product earns {Math.round((p.commission_rate ?? 0) * 100)}% commission
                  {(p.price ?? 0) > 0
                    ? ` ($${((p.price ?? 0) * (p.commission_rate ?? 0)).toFixed(2)}/sale)`
                    : ""}
                  {" "}through Amazon Associates. Connect your account to include
                  your affiliate tag automatically.
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => navigate("/settings/channels#affiliates")}
                  >
                    Connect Amazon Associates
                  </Button>
                  <Button size="sm" variant="ghost" asChild>
                    <a
                      href="https://affiliate-program.amazon.com"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Sign up (free) <ExternalLink className="ml-1 h-3 w-3" />
                    </a>
                  </Button>
                </div>
              </div>
            )}

            {/* Manual commission entry for Shopify / generic products
                (FEATURE 3.3). Only the user can fill this in -- there is
                no published rate to look up for arbitrary stores. */}
            {(p as any).commission_source === "manual" && (
              <div className="rounded-xl border border-border bg-surface p-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-xs text-text-muted">Commission:</span>
                  {p.commission_rate != null && p.commission_rate > 0 ? (
                    <span className="text-xs font-medium text-success">
                      {Math.round(p.commission_rate * 100)}%
                    </span>
                  ) : (
                    <span className="text-xs text-text-muted">Not set</span>
                  )}
                  {!showCommissionEditor && (
                    <button
                      onClick={() => {
                        setCommissionInput(
                          p.commission_rate != null && p.commission_rate > 0
                            ? String(Math.round(p.commission_rate * 100 * 10) / 10)
                            : "",
                        );
                        setShowCommissionEditor(true);
                      }}
                      className="ml-auto text-[11px] text-accent hover:underline"
                    >
                      {p.commission_rate != null && p.commission_rate > 0
                        ? "Edit"
                        : "Set commission %"}
                    </button>
                  )}
                </div>
                {showCommissionEditor && (
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={0.5}
                      value={commissionInput}
                      onChange={(e) => setCommissionInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveManualCommission();
                        if (e.key === "Escape") setShowCommissionEditor(false);
                      }}
                      placeholder="e.g. 15"
                      className="w-20 rounded-md border border-border bg-card px-2 py-1 text-xs text-text"
                      autoFocus
                    />
                    <span className="text-xs text-text-muted">%</span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={saveManualCommission}
                      disabled={updateProductMutation.isPending}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setShowCommissionEditor(false)}
                    >
                      Cancel
                    </Button>
                  </div>
                )}
              </div>
            )}

            {/* Description */}
            {p.description && (
              <div>
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide mb-1.5">Description</h4>
                <p className="text-sm text-text-dim leading-relaxed whitespace-pre-wrap">{p.description}</p>
              </div>
            )}

            {/* Details */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">Details</h4>
                {(p.product_url || p.tiktok_product_url) && (
                  <button
                    type="button"
                    onClick={() => refreshMutation.mutate()}
                    disabled={refreshMutation.isPending}
                    className="text-[11px] text-accent hover:underline disabled:opacity-50 disabled:cursor-not-allowed"
                    title="Re-fetch latest data from the source URL"
                  >
                    {refreshMutation.isPending ? "Refreshing…" : "Refresh data"}
                  </button>
                )}
              </div>
              <div className="space-y-1.5 text-sm">
                {p.category && (
                  <div className="flex justify-between">
                    <span className="text-text-muted">Category</span>
                    <span className="text-text">{p.category}</span>
                  </div>
                )}
                {p.seller_name && (
                  <div className="flex justify-between">
                    <span className="text-text-muted">Seller</span>
                    <span className="text-text">{p.seller_name}</span>
                  </div>
                )}
                {p.tiktok_product_id && (
                  <div className="flex justify-between">
                    <span className="text-text-muted">TikTok ID</span>
                    <span className="text-text font-mono text-xs">{p.tiktok_product_id}</span>
                  </div>
                )}
                {productUrl ? (
                  <div className="flex justify-between items-center">
                    <span className="text-text-muted">Product URL</span>
                    <a
                      href={productUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-accent hover:underline text-xs"
                    >
                      {productLinkLabel(productUrl)} <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                ) : p.tiktok_product_id && p.tiktok_product_id !== "sample_003" ? (
                  <div className="flex justify-between items-center">
                    <span className="text-text-muted">TikTok</span>
                    <a
                      href={`https://shop.tiktok.com/view/product/${p.tiktok_product_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-accent hover:underline text-xs"
                    >
                      View on TikTok <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                ) : null}
              </div>
            </div>

            {/* Assets Management */}
            <div className="space-y-3">
              <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">Assets</h4>

              {/* Cover Image */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-dim">Cover Image</span>
                  <label className="cursor-pointer text-xs text-accent hover:underline">
                    {coverUrl ? "Replace" : "Upload"}
                    <input type="file" accept="image/*" className="hidden" onChange={(e) => handleUploadCover(e.target.files?.[0])} />
                  </label>
                </div>
                {!coverUrl && (
                  <div className="w-full aspect-video rounded-lg bg-surface flex items-center justify-center border border-dashed border-border">
                    <label className="cursor-pointer text-center p-4">
                      <ImageIcon className="mx-auto h-6 w-6 text-text-muted mb-1" />
                      <p className="text-xs text-text-muted">Click to upload cover</p>
                      <input type="file" accept="image/*" className="hidden" onChange={(e) => handleUploadCover(e.target.files?.[0])} />
                    </label>
                  </div>
                )}
              </div>

              {/* Additional Images */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-dim">
                    Images ({p.assets?.filter((a: any) => a.media_type === "image").length || 0})
                  </span>
                  <label className="cursor-pointer text-xs text-accent hover:underline">
                    + Add
                    <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => handleUploadImages(e.target.files)} />
                  </label>
                </div>
                {(p.assets?.filter((a: any) => a.media_type === "image").length ?? 0) > 0 && (
                  <div className="grid grid-cols-3 gap-2">
                    {(p.assets ?? []).filter((a: any) => a.media_type === "image").map((asset: any) => (
                      <div key={asset.id} className="relative group">
                        <img
                          src={asset.r2_url || cdnUrl(asset.r2_key)}
                          className="aspect-square rounded-md object-cover cursor-zoom-in"
                          alt=""
                          onClick={() => setLightboxUrl(asset.r2_url || cdnUrl(asset.r2_key))}
                        />
                        <button
                          onClick={() => deleteAssetMutation.mutate(asset.id)}
                          className="absolute top-1 right-1 hidden group-hover:flex h-5 w-5 items-center justify-center rounded-full bg-red-500 text-white text-xs"
                        >×</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Videos */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-dim">
                    Videos ({(p.assets ?? []).filter((a: any) => a.media_type === "video").length})
                  </span>
                  <label className="cursor-pointer text-xs text-accent hover:underline">
                    + Upload video
                    <input type="file" accept="video/*" className="hidden" onChange={(e) => handleUploadVideo(e.target.files?.[0])} />
                  </label>
                </div>
                {(p.assets ?? []).filter((a: any) => a.media_type === "video").length > 0 ? (
                  <div className="space-y-2">
                    {(p.assets ?? []).filter((a: any) => a.media_type === "video").map((asset: any) => (
                      <div key={asset.id} className="relative group">
                        <video
                          src={asset.r2_url || cdnUrl(asset.r2_key)}
                          controls
                          className="w-full rounded-lg"
                        />
                        <button
                          onClick={() => deleteAssetMutation.mutate(asset.id)}
                          className="absolute top-2 right-2 hidden group-hover:flex h-6 w-6 items-center justify-center rounded-full bg-red-500 text-white text-xs"
                        >×</button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="w-full aspect-video rounded-lg bg-surface flex items-center justify-center border border-dashed border-border">
                    <label className="cursor-pointer text-center p-4">
                      <Film className="mx-auto h-6 w-6 text-text-muted mb-1" />
                      <p className="text-xs text-text-muted">Upload a demo video</p>
                      <input type="file" accept="video/*" className="hidden" onChange={(e) => handleUploadVideo(e.target.files?.[0])} />
                    </label>
                  </div>
                )}
              </div>
            </div>

            {/* AI Generation */}
            <div className="space-y-3">
              <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">AI Generate</h4>

              {/* Generate Product Photos */}
              <div className="space-y-2">
                <p className="text-xs text-text-muted">Generate professional product photos using AI</p>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { style: "product_shot", label: "Studio Shot", icon: Camera },
                    { style: "lifestyle", label: "Lifestyle", icon: Sparkles },
                    { style: "swatch", label: "Close-up", icon: ZoomIn },
                  ].map((opt) => (
                    <Button
                      key={opt.style}
                      variant="outline"
                      size="sm"
                      className="flex-col h-auto py-3 text-xs"
                      onClick={() => generateImageMutation.mutate(opt.style)}
                      disabled={generateImageMutation.isPending}
                    >
                      <opt.icon className="h-4 w-4 mb-1" />
                      {opt.label}
                    </Button>
                  ))}
                </div>

                {/* Custom prompt */}
                <div className="flex gap-2">
                  <Input
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    placeholder="Custom prompt: e.g. 'product on marble table, warm lighting'"
                    className="text-xs"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => generateImageMutation.mutate("custom")}
                    disabled={!customPrompt.trim() || generateImageMutation.isPending}
                  >
                    Generate
                  </Button>
                </div>
              </div>

              {/* Background Removal */}
              {coverUrl && (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full text-xs"
                  onClick={() => removeBackgroundMutation.mutate()}
                  disabled={removeBackgroundMutation.isPending}
                >
                  {removeBackgroundMutation.isPending ? (
                    <><Loader2 className="h-3 w-3 mr-1 animate-spin" /> Removing background...</>
                  ) : (
                    <><Scissors className="h-3 w-3 mr-1" /> Remove Background from Cover</>
                  )}
                </Button>
              )}

              {/* Generate Demo Video */}
              <div className="space-y-2">
                <p className="text-xs text-text-muted">Generate a short product demo video</p>
                <div className="grid grid-cols-4 gap-1.5">
                  {([
                    { value: "product_showcase", label: "Showcase" },
                    { value: "lifestyle", label: "Lifestyle" },
                    { value: "unboxing", label: "Reveal" },
                    { value: "comparison", label: "Compare" },
                  ] as const).map((opt) => (
                    <Button
                      key={opt.value}
                      size="sm"
                      variant={videoStyle === opt.value ? "default" : "outline"}
                      className="text-[10px] py-1.5"
                      onClick={() => setVideoStyle(opt.value)}
                    >
                      {opt.label}
                    </Button>
                  ))}
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-text-muted">Length:</span>
                  {([5, 10] as const).map((secs) => (
                    <Button
                      key={secs}
                      size="sm"
                      variant={videoDuration === secs ? "default" : "outline"}
                      className="text-[10px] py-1 px-2.5 h-auto"
                      onClick={() => setVideoDuration(secs)}
                    >
                      {secs}s
                    </Button>
                  ))}
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-text-muted">Speed:</span>
                  {([
                    { value: "fast", label: "Fast" },
                    { value: "pro", label: "Quality" },
                  ] as const).map((opt) => (
                    <Button
                      key={opt.value}
                      size="sm"
                      variant={videoQuality === opt.value ? "default" : "outline"}
                      className="text-[10px] py-1 px-2.5 h-auto"
                      onClick={() => setVideoQuality(opt.value)}
                    >
                      {opt.label}
                    </Button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Input
                    value={videoPrompt}
                    onChange={(e) => { setVideoPrompt(e.target.value); setVideoStyle("custom"); }}
                    placeholder="Custom prompt..."
                    className="text-xs"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => generateVideoMutation.mutate({ style: videoStyle, customPrompt: videoPrompt })}
                    disabled={generateVideoMutation.isPending || (!coverUrl && !p?.cover_image_url) || (videoStyle === "custom" && !videoPrompt.trim())}
                  >
                    {generateVideoMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <><Film className="h-3 w-3 mr-1" />Gen</>}
                  </Button>
                </div>
                <p className="text-[10px] text-text-muted">
                  {generateVideoMutation.isPending ? "Generating... " : ""}
                  {videoQuality === "pro"
                    ? `Quality mode, ${videoDuration}s clip: roughly ${videoDuration === 10 ? "5-6" : "3-4"} minutes.`
                    : "Fast mode: quicker than Quality, at some cost to motion smoothness."}
                </p>
              </div>
            </div>


            {/* Selling Points / Highlights */}
            {(p.selling_points?.length ?? 0) > 0 && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">Highlights</h4>
                <ul className="space-y-1">
                  {p.selling_points!.map((pt: string, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-text">
                      <span className="mt-0.5 h-1.5 w-1.5 rounded-full bg-accent shrink-0" />
                      {pt}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Full Description */}
            {(p.full_description || p.description) && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">Description</h4>
                <p className="text-xs text-text-muted leading-relaxed whitespace-pre-line">
                  {p.full_description || p.description}
                </p>
              </div>
            )}

            {/* Variants */}
            {(p.variants?.length ?? 0) > 0 && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">
                  Variants ({p.variants!.length})
                </h4>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {p.variants!.slice(0, 20).map((v: any, i: number) => (
                    <div key={i} className="shrink-0 rounded-md border border-border bg-surface px-2 py-1.5 text-center min-w-[72px]">
                      <p className="text-[10px] text-text leading-tight">{v.name}</p>
                      {v.price && <p className="text-[10px] text-accent mt-0.5">{v.price}</p>}
                      {v.stockStatus && (
                        <span className={v.stockStatus === "in_stock" ? "text-[9px] text-green-400" : "text-[9px] text-red-400"}>
                          {v.stockStatus === "in_stock" ? "In stock" : "Out"}
                        </span>
                      )}
                    </div>
                  ))}
                  {p.variants!.length > 20 && (
                    <div className="shrink-0 flex items-center px-2 text-xs text-text-muted">+{p.variants!.length - 20} more</div>
                  )}
                </div>
              </div>
            )}

            {/* Specifications */}
            {(p.specifications?.length ?? 0) > 0 && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">Specifications</h4>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  {p.specifications!.map((spec: any, i: number) => (
                    <div key={i} className="flex flex-col">
                      <span className="text-[10px] text-text-muted">{spec.name}</span>
                      <span className="text-xs text-text">{spec.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Seller Info */}
            {(p.seller_name || p.shop_rating || p.shop_followers) && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide">Seller</h4>
                <div className="rounded-lg border border-border bg-surface p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs font-medium text-text">{p.seller_name}</p>
                      {p.shop_identity_label && (
                        <span className="text-[10px] text-amber-400">{p.shop_identity_label}</span>
                      )}
                    </div>
                    <div className="text-right">
                      {p.shop_rating && <p className="text-xs text-text">&#9733; {p.shop_rating}</p>}
                      {p.shop_followers && (
                        <p className="text-[10px] text-text-muted">
                          {p.shop_followers >= 1000 ? (p.shop_followers / 1000).toFixed(1) + "K" : p.shop_followers} followers
                        </p>
                      )}
                    </div>
                  </div>
                  {p.store_sub_scores && Object.keys(p.store_sub_scores).length > 0 && (
                    <div className="space-y-1.5">
                      {Object.entries(p.store_sub_scores as Record<string, {percentage: string}>).map(([name, val]) => (
                        <div key={name}>
                          <div className="flex justify-between text-[10px] text-text-muted mb-0.5">
                            <span>{name}</span><span>{val.percentage}%</span>
                          </div>
                          <div className="h-1 rounded-full bg-surface-raised overflow-hidden">
                            <div className="h-full rounded-full bg-accent" style={{ width: Math.min(100, parseInt(val.percentage) || 0) + "%" }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Casts using this product */}
            {p.casts && p.casts.length > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-text-dim uppercase tracking-wide mb-2">Used in Casts</h4>
                <div className="space-y-1.5">
                  {p.casts.map((cast) => (
                    <div key={cast.id} className="flex items-center justify-between text-sm">
                      <span className="text-text truncate">{cast.name}</span>
                      <Badge variant="secondary" className="text-[10px] capitalize">
                        {cast.status.replace(/_/g, " ")}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* AI-generated sample reviews (Feature 4) */}
            <ProductReviews product={p} />

            {/* Delete Button */}
            <div className="pt-2 border-t border-border">
              <Button
                variant="ghost"
                className="w-full text-danger hover:bg-danger/10 hover:text-danger"
                onClick={async () => {
                  if (await confirmAction({
                    title: "Remove this product?",
                    text: "It won't affect existing casts.",
                    confirmButtonText: "Remove",
                  })) deleteMutation.mutate();
                }}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="h-4 w-4 mr-2" />
                {deleteMutation.isPending ? "Deleting..." : "Delete from Library"}
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Image Lightbox */}
      {lightboxUrl && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
          onClick={() => setLightboxUrl(null)}
        >
          <button
            onClick={() => setLightboxUrl(null)}
            className="absolute top-4 right-4 flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
          <img
            src={lightboxUrl}
            alt=""
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}

// ═══════════════════════════════════════════
//  PRODUCT REVIEWS (AI-generated samples, on click)
// ═══════════════════════════════════════════

type SampleReview = {
  id: string;
  stars: number;
  author: string;
  text: string;
  verified: boolean;
  date: string;
};

function ProductReviews({ product }: { product: ProductDetailResponse }) {
  const [reviews, setReviews] = useState<SampleReview[]>([]);
  const [loading, setLoading] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"newest" | "highest" | "lowest">("highest");
  const [verifiedOnly, setVerifiedOnly] = useState(false);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await productsApi.generateReviews(product.id);
      setReviews(res.reviews);
      setGenerated(true);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      const detail = err?.response?.data?.detail || err?.message || "Couldn't generate reviews right now.";
      setError(detail);
      Sentry.captureException(e);
    } finally {
      setLoading(false);
    }
  };

  const visibleReviews = useMemo(() => {
    let list = reviews;
    if (verifiedOnly) list = list.filter((r) => r.verified);
    const arr = [...list];
    if (sortBy === "highest") arr.sort((a, b) => b.stars - a.stars);
    else if (sortBy === "lowest") arr.sort((a, b) => a.stars - b.stars);
    else if (sortBy === "newest") arr.sort((a, b) => +new Date(b.date) - +new Date(a.date));
    return arr;
  }, [reviews, sortBy, verifiedOnly]);

  const rating = product.rating || 0;
  const reviewCount = product.review_count || 0;

  return (
    <div className="pt-4 mt-2 border-t border-border">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-text">Customer Reviews</h3>
        {!generated && (
          <Button
            size="sm"
            variant="outline"
            onClick={handleGenerate}
            disabled={loading}
          >
            {loading ? (
              <>
                <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <MessageSquare className="w-3 h-3 mr-1" />
                Load sample reviews
              </>
            )}
          </Button>
        )}
      </div>

      {/* Star distribution bar — only when product has a rating */}
      {rating > 0 && (
        <div className="flex items-center gap-3 mb-4">
          <div className="text-2xl font-bold text-text">{rating.toFixed(1)}</div>
          <div className="flex gap-0.5">
            {[1, 2, 3, 4, 5].map((s) => (
              <Star
                key={s}
                className={cn(
                  "w-4 h-4",
                  s <= Math.round(rating)
                    ? "text-yellow-400 fill-yellow-400"
                    : "text-white/10"
                )}
              />
            ))}
          </div>
          {reviewCount > 0 && (
            <span className="text-xs text-white/30">
              {reviewCount.toLocaleString()} reviews
            </span>
          )}
        </div>
      )}

      {/* Inline error */}
      {error && (
        <div className="mb-3 rounded-md border border-danger/40 bg-danger/5 p-2 text-xs text-danger">
          <div>{error}</div>
          <button
            onClick={handleGenerate}
            className="mt-1 underline hover:no-underline"
            disabled={loading}
          >
            Try again
          </button>
        </div>
      )}

      {/* Empty after generation */}
      {generated && reviews.length === 0 && !loading && !error && (
        <div className="text-xs text-text-muted py-2">
          Could not generate reviews. Try again later.
        </div>
      )}

      {/* Filters — only when reviews loaded */}
      {generated && reviews.length > 0 && (
        <div className="flex items-center gap-3 mb-2 text-xs text-text-dim">
          <label className="flex items-center gap-1">
            Sort:
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as "newest" | "highest" | "lowest")}
              className="rounded border border-border bg-surface px-1 py-0.5 text-text"
            >
              <option value="highest">Highest rated</option>
              <option value="lowest">Lowest rated</option>
              <option value="newest">Newest</option>
            </select>
          </label>
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={verifiedOnly}
              onChange={(e) => setVerifiedOnly(e.target.checked)}
            />
            Verified only
          </label>
        </div>
      )}

      {/* Review list */}
      {visibleReviews.map((r) => (
        <div key={r.id} className="py-3 border-b border-white/5">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <div className="flex gap-0.5">
              {[1, 2, 3, 4, 5].map((s) => (
                <Star
                  key={s}
                  className={cn(
                    "w-3 h-3",
                    s <= r.stars
                      ? "text-yellow-400 fill-yellow-400"
                      : "text-white/10"
                  )}
                />
              ))}
            </div>
            <span className="text-xs font-medium text-white/60">{r.author}</span>
            <span className="text-[10px] text-white/20">{r.date}</span>
            {r.verified && (
              <span className="text-[10px] text-emerald-400">Verified Purchase</span>
            )}
          </div>
          <p className="text-xs text-white/50 leading-relaxed">{r.text}</p>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════
//  PRODUCT MEDIA GALLERY GRID (detail page)
// ═══════════════════════════════════════════

function ProductMediaGalleryGrid({ product }: { product: ProductDetailResponse }) {
  // Prefer the dedicated assets endpoint (ordered by position) over the
  // assets embedded in the product detail response so the grid order matches
  // the import order even when callers haven't refreshed the detail query.
  const { data: fetched, isLoading } = useQuery({
    queryKey: ["product-assets", product.id],
    queryFn: () => productsApi.getAssets(product.id),
    staleTime: 60_000,
  });

  const assets: ProductAsset[] = fetched ?? product.assets ?? [];

  // Fallback to bare cover when nothing has been materialised yet (legacy
  // product imported before the multi-asset import path landed).
  if (!isLoading && assets.length === 0) {
    if (product.cover_image_url || product.cover_image_key) {
      return (
        <div className="grid grid-cols-3 gap-2">
          <div className="aspect-square rounded-xl overflow-hidden bg-white/5">
            <img
              src={product.cover_image_url || cdnUrl(product.cover_image_key)}
              alt={product.name}
              className="w-full h-full object-cover"
            />
          </div>
        </div>
      );
    }
    return (
      <div className="aspect-square rounded-xl bg-surface flex items-center justify-center">
        <Package className="h-16 w-16 text-text-muted" />
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="aspect-square rounded-xl bg-surface animate-pulse" />
        ))}
      </div>
    );
  }

  const photoCount = assets.filter((a) => a.media_type === "image").length;
  const videoCount = assets.filter((a) => a.media_type === "video").length;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        {assets.map((asset) => (
          <div
            key={asset.id}
            className="aspect-square rounded-xl overflow-hidden bg-white/5 relative"
          >
            {asset.media_type === "video" ? (
              <video
                src={asset.r2_url || cdnUrl(asset.r2_key)}
                muted
                playsInline
                className="w-full h-full object-cover"
                onMouseEnter={(e) => {
                  e.currentTarget.play().catch(() => undefined);
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.pause();
                  e.currentTarget.currentTime = 0;
                }}
              />
            ) : (
              <img
                src={asset.r2_url || cdnUrl(asset.r2_key)}
                alt={asset.asset_type}
                className="w-full h-full object-cover"
              />
            )}
          </div>
        ))}
      </div>
      <p className="text-xs text-text-muted">
        {photoCount} {photoCount === 1 ? "photo" : "photos"} ·{" "}
        {videoCount} {videoCount === 1 ? "video" : "videos"}
      </p>
    </div>
  );
}
