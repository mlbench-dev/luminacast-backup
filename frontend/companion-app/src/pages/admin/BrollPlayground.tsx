// B-roll Pipeline Playground (admin) — traces the FULL pipeline for one
// prompt + product: the real outline generator, the real script generator,
// and for every block that would get B-roll, every Pexels query tried (in
// production priority order), the raw results each one got back, and how/
// why the final clip was picked. Same production code as a real cast
// (engine.cast_generator.generate_outline / generate_scripts /
// resolve_block_query_candidates) — nothing here touches real
// casts/blocks/billing.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/apiClient";
import { productsApi } from "@/api/productsApi";

interface ProductOption {
  id: string;
  name: string;
}

interface MediaSummary {
  id: number | string;
  kind: "video" | "photo";
  pexels_page_url?: string;
  thumbnail?: string;
  width?: number;
  height?: number;
  duration?: number;
  photographer?: string;
  play_url?: string;
  raw?: unknown;
}

interface QueryAttempt {
  query: string;
  raw_result_count: number;
  raw_candidates: MediaSummary[];
  rerank_enabled: boolean;
  finalists: MediaSummary[] | null;
  vision_pick_index: number | null;
  chosen: MediaSummary | null;
  outcome: string;
  prefilter_error?: string;
}

interface BrollTrace {
  searched_catalog: "photo" | "video";
  block_beat_text: string;
  query_candidates_in_order: string[];
  query_attempts: QueryAttempt[];
  final_pick: { query: string; media: MediaSummary } | null;
}

interface BlockOut {
  index: number;
  block_type: string | null;
  category: string;
  key_points: string[] | null;
  stock_media_query: string | null;
  visual_subject: string | null;
  script_text: string;
  gets_broll: boolean;
  broll: BrollTrace | null;
}

interface RunResult {
  input: { product_id: string; prompt: string; duration_target_seconds: number; orientation: string };
  product: { name: string; cover_image_url: string | null; ai_stock_queries: string[]; ai_visual_description: string };
  blocks: BlockOut[];
}

function MediaCard({ v, label }: { v: MediaSummary; label?: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-black/30 overflow-hidden shrink-0 w-36">
      <div className="aspect-[9/16] bg-black">
        {v.thumbnail && (
          <img src={v.thumbnail} alt="" className="w-full h-full object-cover" />
        )}
      </div>
      <div className="p-1.5 space-y-0.5">
        {label && <div className="text-[9px] font-semibold text-accent">{label}</div>}
        <div className="text-[9px] text-white/50 truncate">id {v.id} · {v.kind}</div>
        <div className="text-[9px] text-white/40">
          {v.width}×{v.height}{v.duration != null ? ` · ${v.duration}s` : ""}
        </div>
        {v.play_url && (
          <a href={v.play_url} target="_blank" rel="noreferrer" className="text-[9px] text-accent/80 hover:text-accent block">
            open ↗
          </a>
        )}
        {v.raw != null && (
          <details className="text-[9px]">
            <summary className="text-white/40 hover:text-white/70 cursor-pointer select-none">raw JSON</summary>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all bg-black/40 rounded p-1.5 text-[8px] text-white/60">
              {JSON.stringify(v.raw, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

function BrollTraceView({ trace }: { trace: BrollTrace }) {
  return (
    <div className="space-y-3">
      <div className="text-xs text-white/60">
        Searched Pexels' <span className="font-semibold text-white/90">{trace.searched_catalog}</span> catalog.
      </div>
      <div className="text-xs text-white/40">
        Beat text fed to the vision re-rank: <span className="text-white/70">{trace.block_beat_text || <em>(empty)</em>}</span>
      </div>
      <ol className="text-xs space-y-1 list-decimal list-inside">
        {trace.query_candidates_in_order.map((q, i) => (
          <li key={i} className={q === trace.final_pick?.query ? "text-accent font-medium" : "text-white/70"}>
            {q}{q === trace.final_pick?.query && "  ← production would stop here"}
          </li>
        ))}
      </ol>
      <div className="space-y-2">
        {trace.query_attempts.map((a, i) => (
          <div key={i} className="rounded-lg border border-white/10 bg-white/[0.03] p-3 space-y-2">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="text-xs font-medium">
                "{a.query}" <span className="text-white/40">— {a.raw_result_count} raw results</span>
              </div>
              <div className={"text-[10px] px-2 py-0.5 rounded-full " +
                (a.raw_result_count > 0 ? "bg-emerald-500/15 text-emerald-300" : "bg-white/10 text-white/40")}>
                {a.outcome}
              </div>
            </div>
            {a.raw_candidates.length > 0 && (
              <div className="flex gap-2 overflow-x-auto pb-1">
                {a.raw_candidates.map((v) => (
                  <MediaCard key={v.id} v={v}
                    label={a.finalists?.some((f) => f.id === v.id) ? "finalist" : undefined} />
                ))}
              </div>
            )}
            {a.vision_pick_index !== null && (
              <div className="text-[10px] text-white/40">
                Vision model picked finalist #{a.vision_pick_index} of {a.finalists?.length}
              </div>
            )}
          </div>
        ))}
      </div>
      {trace.final_pick ? (
        <div className="rounded-lg border border-accent/40 bg-accent/5 p-3 flex items-center gap-3">
          <MediaCard v={trace.final_pick.media} label="final pick" />
          <div className="text-xs text-white/60">Won on query <span className="text-white/90">"{trace.final_pick.query}"</span></div>
        </div>
      ) : (
        <div className="text-xs text-white/50">No query returned results — this block would fall back to avatar-idle B-roll.</div>
      )}
    </div>
  );
}

function BlockCard({ block }: { block: BlockOut }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="w-full text-left p-4 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-xs font-semibold text-white/80">
            Block {block.index + 1} — <span className="text-accent">{block.category}</span>
            {block.block_type && <span className="text-white/40"> ({block.block_type})</span>}
          </div>
          <div className="text-[10px] text-white/40">{block.gets_broll ? (open ? "hide b-roll trace ▲" : "show b-roll trace ▼") : "no b-roll (on-camera avatar)"}</div>
        </div>
        <div className="text-xs text-white/90">{block.script_text || <em className="text-white/30">no script generated</em>}</div>
        {block.key_points && block.key_points.length > 0 && (
          <div className="text-[10px] text-white/40">key points: {block.key_points.join(" · ")}</div>
        )}
        {block.stock_media_query && (
          <div className="text-[10px] text-white/40">outline's stock_media_query: {block.stock_media_query}</div>
        )}
        {block.visual_subject && (
          <div className="text-[10px] text-white/40">visual_subject: {block.visual_subject}</div>
        )}
      </button>
      {open && block.broll && (
        <div className="border-t border-white/10 p-4">
          <BrollTraceView trace={block.broll} />
        </div>
      )}
    </div>
  );
}

export function BrollPlaygroundPage() {
  const [products, setProducts] = useState<ProductOption[]>([]);
  const [productId, setProductId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(30);
  const [orientation, setOrientation] = useState<"portrait" | "landscape">("portrait");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);

  useEffect(() => {
    productsApi.list({ per_page: 100 }).then((r) => {
      setProducts(r.products.map((p: any) => ({ id: p.id, name: p.name })));
      if (r.products[0]) setProductId(r.products[0].id);
    }).catch(() => {});
  }, []);

  const run = useCallback(async () => {
    if (!productId || !prompt.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.post("/dev/broll-playground/run", {
        product_id: productId,
        prompt: prompt.trim(),
        duration_target_seconds: duration,
        orientation,
      });
      setResult(r.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setRunning(false);
    }
  }, [productId, prompt, duration, orientation]);

  return (
    <div className="max-w-5xl mx-auto px-6 py-6 space-y-5 text-white">
      <header>
        <h1 className="text-xl font-semibold">B-roll Pipeline Playground</h1>
        <p className="text-xs text-white/40 mt-0.5">
          Runs the real outline + script generation for a prompt + product,
          then traces the real B-roll query pipeline for every block. Admin only.
        </p>
      </header>

      <div className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <div>
          <label className="text-[11px] text-white/50 block mb-1">Product</label>
          <select
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
            className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm"
          >
            {products.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-[11px] text-white/50 block mb-1">
            Cast prompt — the same creative brief you'd type when creating a real cast
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder='e.g. "Promote these kids tracksuits — show them being worn, comfortable and durable for active play"'
            className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm placeholder:text-white/25 resize-none"
          />
        </div>

        <div className="flex items-center gap-3">
          <div>
            <label className="text-[11px] text-white/50 block mb-1">Duration target (s)</label>
            <input type="number" value={duration} onChange={(e) => setDuration(Number(e.target.value) || 30)}
              className="w-24 bg-black/30 border border-white/10 rounded-lg px-3 py-1.5 text-sm" />
          </div>
          <div>
            <label className="text-[11px] text-white/50 block mb-1">Orientation</label>
            <select value={orientation} onChange={(e) => setOrientation(e.target.value as any)}
              className="bg-black/30 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
              <option value="portrait">portrait</option>
              <option value="landscape">landscape</option>
            </select>
          </div>
        </div>
      </div>

      <button
        onClick={run}
        disabled={!productId || !prompt.trim() || running}
        className="text-sm font-medium px-4 py-2 rounded-lg bg-accent hover:bg-accent/90 disabled:opacity-40"
      >
        {running ? "Generating outline + scripts + b-roll…" : "Run"}
      </button>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-4">
          <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-white/50">
              What the AI decided from the product photo
            </h2>
            <div className="flex gap-3">
              {result.product.cover_image_url && (
                <img src={result.product.cover_image_url} alt="" className="w-20 h-20 rounded-lg object-cover border border-white/10 shrink-0" />
              )}
              <div className="text-xs space-y-1.5 min-w-0">
                <div><span className="text-white/40">Visual description: </span>{result.product.ai_visual_description || <em className="text-white/30">none cached</em>}</div>
                <div>
                  <span className="text-white/40">Product-level queries: </span>
                  {result.product.ai_stock_queries?.length
                    ? result.product.ai_stock_queries.join(" · ")
                    : <em className="text-white/30">none cached</em>}
                </div>
              </div>
            </div>
          </section>

          <h2 className="text-xs font-semibold uppercase tracking-wider text-white/50">
            Blocks ({result.blocks.length}) — click to expand a block's b-roll trace
          </h2>
          <div className="space-y-2">
            {result.blocks.map((b) => (
              <BlockCard key={b.index} block={b} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default BrollPlaygroundPage;
