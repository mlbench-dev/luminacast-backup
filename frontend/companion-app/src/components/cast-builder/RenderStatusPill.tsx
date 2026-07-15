/**
 * RenderStatusPill — header pill that shows in-flight render status.
 *
 * Compact view (always visible):
 *   [progress bar across the pill] Baking 2/3 · ~45s
 *
 * Clicking the pill toggles a portaled dropdown with per-block rows:
 *   ✅ Block 1  · Host · 12s
 *   🟠 Block 2  · Pod  · baking · ~30s
 *   ⚪ Block 3  · —    · queued · ~60s
 *
 * Designed to handle 100+ blocks:
 *   - Compact view only shows aggregate counts + ETA, never per-block chips.
 *   - Dropdown is scrollable.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, CheckCircle2, XCircle, Clock, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

export type RenderBlockStatus = {
  block_id: string;
  index: number;
  state: "queued" | "baking" | "done" | "failed";
  provider: string | null; // "host" | "mod" | "pod" | "cache" | "voice" | null
  started_at: string | null;
  completed_at: string | null;
  duration_s: number | null;
  eta_seconds?: number | null;
  error?: string | null;
  category?: string | null;
};

export type RenderStatusPillProps = {
  /** Current render status label. "queued" shows a different pill. */
  renderStatusRaw?: string;
  progressPercent?: number;
  progressStep?: string;
  queuePosition?: number | null;
  etaSeconds?: number | null;
  blocks?: RenderBlockStatus[];
  /** Overall baking counts from the cast_renders row (fallback for servers
   * that haven't written block_statuses yet — or completed renders). */
  bakingCompleted?: number;
  bakingTotal?: number;
};

const PROVIDER_STYLES: Record<string, string> = {
  host: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  mod:  "bg-white/5 text-white/40 border-white/10",
  pod:  "bg-purple-500/15 text-purple-300 border-purple-500/30",
  fal:  "bg-blue-500/15 text-blue-300 border-blue-500/30",
  cache:"bg-white/10 text-white/60 border-white/20",
  voice:"bg-amber-500/15 text-amber-300 border-amber-500/30",
};

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 10) return `${m}m ${s}s`;
  return `${m}m`;
}

function formatElapsed(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

const CATEGORY_LABELS: Record<string, string> = {
  avatar_speaking: "Speaking",
  avatar_voiceover: "Voiceover",
  avatar_action: "Action",
  // Legacy aliases — old blocks render correctly without a DB rewrite.
  avatar_motion: "Action",
  avatar_acting: "Action",
  pip_talking_head: "Talking head",
  stock_video: "Stock video",
  stock_photo: "Stock photo",
  product: "Product",
  music: "Music",
};

function formatCategoryLabel(category: string | null | undefined): string {
  if (!category) return "block";
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

function ProviderChip({ provider }: { provider: string | null }) {
  if (!provider) return <span className="text-white/30 text-[10px]">—</span>;
  const cls = PROVIDER_STYLES[provider] ?? "bg-white/10 text-white/60 border-white/20";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide ${cls}`}>
      {provider}
    </span>
  );
}

function BlockRow({ block, isNext }: { block: RenderBlockStatus; isNext?: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (block.state !== "baking" || !block.started_at) return;
    const start = new Date(block.started_at).getTime();
    setElapsed(Math.floor((Date.now() - start) / 1000));
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [block.state, block.started_at]);

  const icon =
    block.state === "done"   ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" /> :
    block.state === "failed" ? <XCircle      className="w-3.5 h-3.5 text-red-400 shrink-0" /> :
    block.state === "baking" ? <Loader2      className="w-3.5 h-3.5 text-amber-400 shrink-0 animate-spin" /> :
    isNext                   ? <Clock        className="w-3.5 h-3.5 text-amber-300 shrink-0 animate-pulse" /> :
                               <Clock        className="w-3.5 h-3.5 text-white/20 shrink-0" />;

  const rightLabel =
    block.state === "done"   ? `${Math.round(block.duration_s ?? 0)}s` :
    block.state === "baking" ? formatElapsed(elapsed) :
    block.state === "failed" ? "failed" :
    isNext                   ? "next" :
                               "";

  const categoryLabel = formatCategoryLabel(block.category);

  return (
    <div className={cn(
      "flex items-center gap-2 px-3 py-1.5 text-xs border-b border-white/5 last:border-0",
      block.state === "baking" && "bg-amber-500/5",
      isNext && "bg-amber-500/5 border-l-2 border-l-amber-400",
      block.state === "done" && "opacity-70",
    )}>
      {icon}
      <span className="text-white/70 tabular-nums w-8 shrink-0">#{(block.index ?? 0) + 1}</span>
      <span className="text-white/30 truncate flex-1 min-w-0">{categoryLabel}</span>

      {block.state === "baking" && (
        <div className="w-16 h-1 bg-white/10 rounded-full overflow-hidden flex-shrink-0">
          <div className="h-full bg-amber-400 rounded-full animate-pulse" style={{ width: "60%" }} />
        </div>
      )}

      <ProviderChip provider={block.provider} />
      <span className={cn(
        "tabular-nums w-14 text-right shrink-0",
        block.state === "baking" ? "text-amber-300 font-medium" : "text-white/40",
      )}>
        {rightLabel}
      </span>
    </div>
  );
}

export function RenderStatusPill({
  renderStatusRaw,
  progressPercent = 0,
  progressStep,
  queuePosition,
  etaSeconds,
  blocks = [],
  bakingCompleted = 0,
  bakingTotal = 0,
}: RenderStatusPillProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef   = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);

  // Position the dropdown under the pill.
  useLayoutEffect(() => {
    if (!open) return;
    const el = triggerRef.current;
    if (!el) return;
    const update = () => {
      const r = el.getBoundingClientRect();
      setPos({ top: r.bottom + 6, left: r.left, width: Math.max(r.width, 320) });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Block counts — compute from blocks if present, otherwise fall back to
  // bakingCompleted/bakingTotal from the renders row.
  const counts = useMemo(() => {
    if (blocks.length > 0) {
      let done = 0, baking = 0, queued = 0, failed = 0;
      for (const b of blocks) {
        if (b.state === "done") done++;
        else if (b.state === "baking") baking++;
        else if (b.state === "queued") queued++;
        else if (b.state === "failed") failed++;
      }
      return { done, baking, queued, failed, total: blocks.length };
    }
    return {
      done: bakingCompleted, baking: 0, queued: 0, failed: 0,
      total: bakingTotal || 0,
    };
  }, [blocks, bakingCompleted, bakingTotal]);

  // ETA estimate: average duration of done blocks × remaining count.
  // Fallback to 120s/block if no blocks have completed yet.
  const remainingLabel = useMemo(() => {
    const doneBlocks = blocks.filter(b => b.state === "done");
    const doneAvgSecs = doneBlocks.length > 0
      ? doneBlocks.reduce((s, b) => s + (b.duration_s ?? 0), 0) / doneBlocks.length
      : 120;
    const remainingSecs = (counts.queued + counts.baking) * doneAvgSecs;
    if (remainingSecs <= 0) return "done";
    return remainingSecs > 60
      ? `~${Math.round(remainingSecs / 60)}m left`
      : `~${Math.round(remainingSecs)}s left`;
  }, [blocks, counts.queued, counts.baking]);

  // Queued (not started) — different pill, no bar yet.
  if (renderStatusRaw === "queued") {
    return (
      <div className="relative inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 text-amber-300 text-xs font-medium whitespace-nowrap">
        <Clock className="w-3.5 h-3.5" />
        <span>Queued #{queuePosition ?? "…"}</span>
      </div>
    );
  }

  // Label rules:
  //   - composing → "Composing · ~45s"
  //   - baking    → "Baking X/Y · ~45s"
  //   - fallback  → whatever the backend sent
  const primaryLabel =
    renderStatusRaw === "composing"
      ? `Composing${etaSeconds != null ? ` · ~${formatEta(etaSeconds)}` : ""}`
      : counts.total > 0
        ? `Baking ${counts.done}/${counts.total}${etaSeconds != null ? ` · ~${formatEta(etaSeconds)}` : ""}`
        : (progressStep || "Rendering…");

  const panel = open && pos && createPortal(
    <div
      ref={panelRef}
      className="fixed z-[9999] rounded-lg border border-white/10 bg-neutral-950/95 backdrop-blur-md shadow-2xl"
      style={{ top: pos.top, left: pos.left, width: pos.width, maxHeight: 420 }}
      role="dialog"
    >
      <div className="px-3 py-2 border-b border-white/10 flex flex-col gap-1">
        <span className="text-xs text-white/70 font-medium">Render progress</span>
        <span className="text-[10px] text-white/40">
          {counts.done}/{counts.total} done · {counts.baking} baking · {counts.queued} queued · {remainingLabel}
          {counts.failed > 0 && <span className="text-red-400"> · {counts.failed} failed</span>}
        </span>
      </div>
      {blocks.length === 0 ? (
        <div className="px-3 py-4 text-xs text-white/40 text-center">Waiting for blocks to start…</div>
      ) : (
        <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
          {blocks.map((b, i) => {
            const isNext = b.state === "queued" && !blocks.slice(0, i).some(x => x.state === "queued");
            return <BlockRow key={b.block_id} block={b} isNext={isNext} />;
          })}
        </div>
      )}
      <div className="flex items-center gap-3 text-[10px] text-white/30 px-3 py-2 border-t border-white/10">
        <span><span className="text-emerald-300">●</span> host = free GPU</span>
        <span><span className="text-purple-300">●</span> pod = cloud GPU ($)</span>
        <span><span className="text-blue-300">●</span> fal = serverless ($)</span>
        <span><span className="text-white/20">●</span> mod = parked</span>
      </div>
    </div>,
    document.body
  );

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(v => !v)}
        className="relative overflow-hidden inline-flex items-center gap-1.5 min-w-0 max-w-[260px] px-3 py-1.5 rounded-md border border-white/20 bg-white/10 text-white text-xs font-medium hover:bg-white/15 transition-colors"
        data-testid="render-status-pill"
        aria-expanded={open}
      >
        {/* Progress fill — sits behind the text */}
        <span
          className="absolute inset-y-0 left-0 bg-accent/30 transition-all duration-500"
          style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
        />
        <Loader2 className="w-3.5 h-3.5 animate-spin relative shrink-0" />
        <span className="relative truncate">{primaryLabel}</span>
        <ChevronDown className={`w-3.5 h-3.5 relative shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {panel}
    </>
  );
}
