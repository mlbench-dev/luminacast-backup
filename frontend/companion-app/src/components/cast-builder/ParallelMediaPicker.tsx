import { useState, useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, X, Search, Loader2, Image as ImageIcon, Video as VideoIcon, ExternalLink, Sparkles, AlertTriangle } from "lucide-react";
import { castsApi, api, productsApi } from "@/lib/api";
import type { Block, ParallelMediaItem } from "@/lib/types";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { toast } from "@/hooks/useToast";

/**
 * ParallelMediaPicker.
 *
 * Lets the user attach stock photos / videos that play ON TOP of an
 * avatar block (voiceover, PIP, or speaking). The avatar's audio still
 * runs underneath; the visuals here cover the canvas while the voice
 * plays.
 *
 * UI:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ Visual b-roll          [+ Add visual]                   │
 *   │  ┌─────┐ ┌─────┐ ┌─────┐                                │
 *   │  │photo│ │video│ │photo│   each shows kind + duration   │
 *   │  └─────┘ └─────┘ └─────┘                                │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Clicking "Add visual" opens a Pexels search modal. Selecting a result
 * appends it to `block.parallel_media` and persists via PUT /blocks/{id}.
 */
export function ParallelMediaPicker({
  castId,
  block,
  scriptText,
  onUpdated,
}: {
  castId: string;
  block: Block;
  scriptText: string;
  onUpdated: (block: Block) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const items: ParallelMediaItem[] = block.parallel_media || [];

  const persist = useCallback(
    async (next: ParallelMediaItem[]) => {
      try {
        await castsApi.updateBlock(castId, block.id, { parallel_media: next });
        onUpdated({ ...block, parallel_media: next });
      } catch (err: any) {
        toast({
          title: "Could not save b-roll",
          description: err?.response?.data?.detail || err.message,
          variant: "destructive",
        });
      }
    },
    [castId, block, onUpdated],
  );

  const addItem = useCallback(
    async (item: ParallelMediaItem) => {
      const next = [...items, item];
      await persist(next);
      setPickerOpen(false);
    },
    [items, persist],
  );

  const removeItem = useCallback(
    async (idx: number) => {
      const next = items.filter((_, i) => i !== idx);
      await persist(next);
    },
    [items, persist],
  );

  // The Smart Cast outline emits a per-block stock_media_query. Show it
  // as a chip next to the picker button so the user knows what tag the
  // AI used — tap it to re-search if they want, or open the picker to
  // edit. The chip also signals "this isn't random b-roll, it's an AI
  // pick tied to the script".
  const aiQuery = (block.stock_media_query || "").trim();
  const aiSuggestedItem = items.find((it) => it.ai_suggested);

  // AI-from-product b-roll status (Setup → "AI-generated from product").
  // While "generating" we hide the interim stock clip and show a placeholder
  // that polls itself out for the real product shot (poll lives in ScriptPhase).
  const aiBroll = block.metadata?.ai_broll;
  const { data: product } = useQuery({
    queryKey: ["product", block.product_id],
    queryFn: () => productsApi.get(block.product_id as string),
    enabled: !!block.product_id && aiBroll === "generating",
    staleTime: 60_000,
  });
  const productCover =
    product?.cover_image_url ||
    (product?.cover_image_key ? cdnUrl(product.cover_image_key) : null);

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold text-white/80 flex items-center gap-1.5">
            <ImageIcon className="w-3.5 h-3.5 text-accent" />
            Visual b-roll
            {aiQuery && (
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                title={`AI picked b-roll for: “${aiQuery}” — click to browse alternatives`}
                className="inline-flex items-center gap-1 rounded-full bg-fuchsia-500/15 hover:bg-fuchsia-500/25 text-fuchsia-200 ring-1 ring-fuchsia-400/30 px-1.5 py-[1px] text-[9px] font-medium tracking-tight transition normal-case"
              >
                <Sparkles className="w-2.5 h-2.5" />
                <span className="max-w-[160px] truncate">{aiQuery}</span>
              </button>
            )}
          </div>
          <div className="text-[10px] text-white/40">
            {aiBroll === "generating"
              ? "Creating an AI shot of your product for this beat…"
              : aiBroll === "failed"
              ? "Couldn't generate a product shot — using stock for now."
              : aiSuggestedItem
              ? "AI picked the visual below — swap it or add more."
              : aiQuery
              ? "Search ran with the AI tag above — add what fits the moment."
              : "Stock photos / videos that play on top of the voice. Avatar audio keeps running underneath."}
          </div>
        </div>
        <button
          onClick={() => setPickerOpen(true)}
          className="shrink-0 inline-flex items-center gap-1 rounded-md bg-accent/15 hover:bg-accent/25 text-accent px-2.5 py-1 text-xs font-medium transition"
        >
          <Plus className="w-3 h-3" /> Add visual
        </button>
      </div>

      {aiBroll === "generating" && (
        <div className="flex items-center gap-3 rounded-md border border-fuchsia-400/20 bg-fuchsia-500/[0.06] p-2.5">
          <div className="relative h-16 w-24 shrink-0 rounded-md overflow-hidden border border-white/10 bg-black/40">
            {productCover && (
              <img src={productCover} alt="" className="w-full h-full object-cover opacity-40" />
            )}
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-fuchsia-200" />
            </div>
          </div>
          <div className="min-w-0 text-[11px] leading-snug text-fuchsia-100/90">
            Generating a shot of your product for this beat…
            <span className="block text-fuchsia-200/50">
              Takes a few minutes — it&apos;ll appear here on its own.
            </span>
          </div>
        </div>
      )}

      {aiBroll === "failed" && (
        <div className="flex items-start gap-2 rounded-md border border-amber-400/20 bg-amber-500/[0.06] p-2 text-[10px] text-amber-200/90">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>
            AI couldn&apos;t make a product shot for this beat — showing stock. Use
            &ldquo;Add visual&rdquo; to pick one, or check the product has a cover photo.
          </span>
        </div>
      )}

      {aiBroll !== "generating" && items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {items.map((item, idx) => (
            <div
              key={idx}
              className="group relative h-16 w-24 rounded-md overflow-hidden border border-white/10 bg-black/40"
            >
              {item.thumbnail || item.url ? (
                <img
                  src={item.thumbnail || item.url}
                  alt=""
                  className="w-full h-full object-cover"
                  loading="lazy"
                  decoding="async"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  {item.kind === "video" ? (
                    <VideoIcon className="w-4 h-4 text-white/30" />
                  ) : (
                    <ImageIcon className="w-4 h-4 text-white/30" />
                  )}
                </div>
              )}
              <div className="absolute top-0.5 left-0.5 rounded bg-black/70 px-1 py-0.5 text-[8px] uppercase tracking-wider text-white/80">
                {item.kind === "video" ? "vid" : "img"}
              </div>
              <button
                onClick={() => removeItem(idx)}
                className="absolute top-0.5 right-0.5 rounded-full bg-black/70 p-0.5 text-white/70 hover:text-white opacity-0 group-hover:opacity-100 transition"
                title="Remove"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {pickerOpen && (
        <PexelsPickerModal
          // Prefer the AI-picked query (from the Smart Cast outline) over
          // a script-derived guess — the LLM already optimized it for
          // Pexels relevance and length. Script-derived fallback only
          // kicks in for legacy blocks that pre-date Smart Cast.
          initialQuery={aiQuery || defaultQueryFromScript(scriptText)}
          onClose={() => setPickerOpen(false)}
          onPick={addItem}
        />
      )}
    </div>
  );
}

/**
 * First few content words of the block script — used as a Pexels seed
 * when no AI-suggested query exists (legacy blocks). Strips:
 *   - Gesture markers: [gesture:point]
 *   - Prosody tags:    (excited), (casual), (whispering), [pause], etc.
 *   - JSON-style ranges: {1.2-2.0}
 *   - Stop-words and short words
 * The 'casual', 'excited', etc. words from prosody were leaking into
 * search queries before this strip and producing irrelevant results.
 */
function defaultQueryFromScript(text: string): string {
  if (!text) return "";
  const PROSODY_LITERALS = new Set([
    "excited", "casual", "whispering", "laughing", "sighing",
    "pause", "super", "happy", "point", "celebrate", "explode",
    "gesture", "this", "that", "with", "from", "have",
    "will", "your", "about", "like", "just", "more", "very",
  ]);
  const words = text
    // strip gesture/prosody markers entirely
    .replace(/\[(?:gesture|pause)[^\]]*\]/gi, "")
    .replace(/\((?:excited|casual|whispering|laughing|sighing|super happy)\)/gi, "")
    .replace(/\{[^}]*\}/g, "")
    .replace(/[^a-zA-Z\s]/g, " ")
    .split(/\s+/)
    .map((w) => w.toLowerCase())
    .filter((w) => w.length > 3 && !PROSODY_LITERALS.has(w))
    .slice(0, 3);
  return words.join(" ");
}

interface PexelsResult {
  id: number | string;
  type: "photo" | "video";
  src: string;
  thumb: string;
  width?: number;
  height?: number;
  duration?: number;
  photographer?: string;
}

function PexelsPickerModal({
  initialQuery,
  onClose,
  onPick,
}: {
  initialQuery: string;
  onClose: () => void;
  onPick: (item: ParallelMediaItem) => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [tab, setTab] = useState<"video" | "photo">("video");
  const [results, setResults] = useState<PexelsResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  const search = useCallback(async () => {
    if (!query.trim()) return;
    setLoading(true);
    setSearched(true);
    try {
      const path = tab === "video" ? "/stock-media/videos" : "/stock-media/photos";
      const params: Record<string, any> = { q: query.trim(), per_page: 24 };
      // Vertical b-roll over a portrait avatar block.
      params.orientation = "portrait";
      const { data } = await api.get(path, { params });
      setResults(
        (data?.results || []).map((r: any) => ({
          id: r.id,
          type: r.type,
          src: r.src,
          thumb: r.thumb || r.src,
          width: r.width,
          height: r.height,
          duration: r.duration,
          photographer: r.photographer,
        })),
      );
    } catch (err: any) {
      toast({
        title: "Search failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [query, tab]);

  // Auto-search on open if we have a default query.
  useEffect(() => {
    if (initialQuery) search();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-search when tab flips (only if user has searched before).
  useEffect(() => {
    if (searched) search();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const pick = useCallback(
    (r: PexelsResult) => {
      if (!r.src) return;
      onPick({
        kind: r.type,
        url: r.src,
        thumbnail: r.thumb,
        pexels_id: String(r.id),
        source: "pexels",
        start_offset_s: 0,
        duration_s: r.type === "video" ? r.duration ?? null : null,
      });
    },
    [onPick],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-[min(900px,calc(100vw-2rem))] max-h-[85vh] overflow-hidden rounded-2xl border border-white/10 bg-[#0f0f1a] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
          <h3 className="text-sm font-semibold text-white">Find b-roll</h3>
          <span className="text-[10px] text-white/40">via Pexels</span>
          <div className="flex-1" />
          <button
            onClick={onClose}
            className="text-white/40 hover:text-white/80"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5">
          <div className="flex rounded-full bg-white/5 p-0.5 text-xs">
            <button
              onClick={() => setTab("video")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-full transition",
                tab === "video" ? "bg-white/10 text-white" : "text-white/50 hover:text-white/80",
              )}
            >
              <VideoIcon className="w-3 h-3" /> Videos
            </button>
            <button
              onClick={() => setTab("photo")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-full transition",
                tab === "photo" ? "bg-white/10 text-white" : "text-white/50 hover:text-white/80",
              )}
            >
              <ImageIcon className="w-3 h-3" /> Photos
            </button>
          </div>
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/30" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") search();
              }}
              placeholder="Search Pexels — e.g. yoga class, sunset beach…"
              className="w-full rounded-md bg-white/5 border border-white/10 pl-8 pr-3 py-1.5 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-accent/40"
            />
          </div>
          <button
            onClick={search}
            disabled={!query.trim() || loading}
            className="rounded-md bg-accent text-white text-xs font-medium px-3 py-1.5 hover:bg-accent/90 disabled:opacity-40"
          >
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Search"}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && (
            <div className="flex items-center justify-center py-12 text-white/40 text-sm">
              <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Searching…
            </div>
          )}
          {!loading && searched && results.length === 0 && (
            <div className="text-center py-12 text-white/40 text-sm">
              No results. Try a different search.
            </div>
          )}
          {!loading && results.length > 0 && (
            <div className="grid grid-cols-3 sm:grid-cols-4 gap-3">
              {results.map((r) => (
                <button
                  key={`${r.type}_${r.id}`}
                  onClick={() => pick(r)}
                  className="group relative aspect-[3/4] rounded-md overflow-hidden border border-white/10 hover:border-accent/60 transition"
                  title={r.photographer ? `Photo by ${r.photographer}` : undefined}
                >
                  <img
                    src={r.thumb || r.src}
                    alt=""
                    className="w-full h-full object-cover transition-transform group-hover:scale-[1.03]"
                    loading="lazy"
                  />
                  {r.type === "video" && (
                    <div className="absolute top-1 left-1 rounded bg-black/70 px-1 py-0.5 text-[9px] uppercase tracking-wider text-white/90 flex items-center gap-1">
                      <VideoIcon className="w-2.5 h-2.5" />
                      {r.duration ? `${Math.round(r.duration)}s` : "video"}
                    </div>
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent opacity-0 group-hover:opacity-100 transition flex items-end p-2">
                    <span className="text-[10px] font-medium text-white">+ Use</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="border-t border-white/5 px-4 py-2 flex items-center justify-between text-[10px] text-white/30">
          <span>
            Stock content from{" "}
            <a
              href="https://pexels.com"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white/60 inline-flex items-center gap-0.5"
            >
              Pexels <ExternalLink className="w-2.5 h-2.5" />
            </a>{" "}
            · free for commercial use
          </span>
          <span>{results.length > 0 ? `${results.length} results` : ""}</span>
        </div>
      </div>
    </div>
  );
}
