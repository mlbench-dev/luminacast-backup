import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronLeft,
  Film,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Play,
  Volume2,
  AlertTriangle,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { castsApi, historyApi, type HistoryEvent } from "@/lib/api";
import { CastStatus } from "@/lib/types";
import type { Variant, Block, Cast } from "@/lib/types";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";

const QUALITY_LABELS: Record<string, string> = {
  simple: "Simple",
  hd: "HD",
  hd_plus: "HD+",
};

function StatusBadge({ status }: { status: string }) {
  const s = status?.toUpperCase();
  const variant =
    s === "READY" || s === "COMPLETED"
      ? "default"
      : s === "FAILED"
        ? "danger"
        : "secondary";
  return (
    <Badge variant={variant} className="text-[10px]">
      {status}
    </Badge>
  );
}

function VariantCard({ variant }: { variant: Variant }) {
  const hasVideo = variant.video_key && variant.status?.toUpperCase() === "READY";
  const hasAudio = variant.audio_key && !hasVideo;

  return (
    <div className="rounded border border-border bg-surface p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-text">
          Variant {variant.variant_label || "A"}
        </span>
        <div className="flex items-center gap-2">
          <StatusBadge status={variant.status} />
          {variant.composition_warnings && variant.composition_warnings.length > 0 && (
            <div
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 text-xs cursor-help"
              title={variant.composition_warnings.map((w) => `${w.step}: ${w.error}`).join("\n")}
            >
              <AlertTriangle className="h-3 w-3" />
              {variant.composition_warnings.length} warning{variant.composition_warnings.length > 1 ? "s" : ""}
            </div>
          )}
        </div>
      </div>

      {hasVideo && (
        <video
          src={variant.stream_url ? variant.stream_url : cdnUrl(variant.video_key!)}
          controls
          preload="metadata"
          className="w-full rounded mb-2 bg-black"
        />
      )}

      {hasAudio && (
        <div className="flex items-center gap-2 mb-2 rounded bg-card p-2">
          <Volume2 className="h-4 w-4 text-text-muted shrink-0" />
          <audio
            src={cdnUrl(variant.audio_key!)}
            controls
            preload="metadata"
            className="w-full h-8"
          />
        </div>
      )}

      {variant.script_text && (
        <p className="text-xs text-text-dim line-clamp-3">{variant.script_text}</p>
      )}

      <div className="flex items-center gap-3 mt-1">
        {variant.duration_seconds != null && variant.duration_seconds > 0 && (
          <span className="text-[10px] text-text-muted">
            {variant.duration_seconds.toFixed(1)}s
          </span>
        )}
        {variant.generation_error && (
          <span className="text-[10px] text-red-400 truncate" title={variant.generation_error}>
            {variant.generation_error}
          </span>
        )}
      </div>
    </div>
  );
}

export function CastDetailPage() {
  const { castId } = useParams<{ castId: string }>();
  const navigate = useNavigate();

  const { data: cast, isLoading } = useQuery({
    queryKey: ["cast", castId],
    queryFn: () => castsApi.get(castId!),
    enabled: !!castId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === CastStatus.GENERATING ? 3000 : false;
    },
  });

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-accent" />
      </div>
    );
  }

  if (!cast) {
    return (
      <div className="mx-auto max-w-4xl text-center py-20">
        <p className="text-text-dim">Cast not found</p>
        <Button variant="outline" onClick={() => navigate("/cast-builder")} className="mt-4">
          Back to Casts
        </Button>
      </div>
    );
  }

  const isGenerating = cast.status === CastStatus.GENERATING;
  const isReady = cast.status === CastStatus.READY;
  const isFailed = cast.status === CastStatus.GENERATION_FAILED;
  const blocks: Block[] = (cast as any).blocks || [];
  const products: any[] = (cast as any).products || [];

  const totalVariants = blocks.reduce(
    (sum, b) => sum + (b.variants?.length || 0),
    0,
  );
  const readyVariants = blocks.reduce(
    (sum, b) =>
      sum + (b.variants?.filter((v) => v.status?.toUpperCase() === "READY").length || 0),
    0,
  );

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => navigate("/cast-builder")}>
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-text">{cast.name || "Untitled Cast"}</h1>
          <div className="flex items-center gap-2 mt-1">
            <Badge variant={isReady ? "default" : isFailed ? "danger" : "secondary"}>
              {cast.status.replace(/_/g, " ")}
            </Badge>
            {(cast as any).quality && (
              <Badge variant="outline">
                {QUALITY_LABELS[(cast as any).quality] || (cast as any).quality}
              </Badge>
            )}
            {totalVariants > 0 && (
              <span className="text-xs text-text-muted">
                {readyVariants}/{totalVariants} clips ready
              </span>
            )}
          </div>
        </div>
        {isReady && (
          <Button onClick={() => navigate("/live-control")}>
            <Play className="mr-1 h-4 w-4" /> Go Live with this Cast
          </Button>
        )}
      </div>

      {/* Generation progress */}
      {isGenerating && (
        <div className="rounded-lg border border-border bg-card p-6 text-center">
          <Loader2 className="mx-auto h-8 w-8 animate-spin text-accent" />
          <h3 className="mt-3 text-lg font-semibold text-text">Generating Cast</h3>
          <p className="text-sm text-text-dim mt-1">
            {(cast.generation_progress || 0) < 0.5
              ? (cast as any).progress_step || "Generating speech audio..."
              : `Rendering video clips on GPU... ${Math.round((cast.generation_progress || 0) * 100)}%`
            }
          </p>
          <div className="mx-auto max-w-sm mt-4">
            <Progress value={(cast.generation_progress || 0) * 100} />
            <p className="mt-2 text-xs text-text-muted">
              {Math.round((cast.generation_progress || 0) * 100)}% complete
            </p>
          </div>
          {(cast.generation_progress || 0) >= 0.5 && (
            <p className="text-[10px] text-text-muted mt-3">
              This may take 10-40 minutes depending on clip length. You can close this page — we'll keep rendering.
            </p>
          )}
        </div>
      )}

      {/* Failed state */}
      {isFailed && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-6 text-center">
          <AlertCircle className="mx-auto h-8 w-8 text-red-400" />
          <h3 className="mt-3 text-lg font-semibold text-text">Generation Failed</h3>
          <p className="text-sm text-text-dim mt-1">{cast.generation_error}</p>
        </div>
      )}

      {/* Products section */}
      {products.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-text mb-3">Products ({products.length})</h2>
          <div className="flex gap-3 overflow-x-auto pb-1">
            {products.map((p: any) => (
              <div key={p.id} className="shrink-0 rounded-lg border border-border bg-card p-3 w-40">
                {p.cover_image_key ? (
                  <img
                    src={cdnUrl(p.cover_image_key)}
                    alt={p.name}
                    className="aspect-square w-full rounded object-contain bg-surface mb-2"
                  />
                ) : (
                  <div className="aspect-square w-full rounded bg-surface flex items-center justify-center mb-2">
                    <Film className="h-6 w-6 text-text-muted" />
                  </div>
                )}
                <p className="text-xs font-medium text-text line-clamp-2">{p.name}</p>
                <p className="text-xs text-text-dim">${p.price?.toFixed(2)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Blocks with variants */}
      {blocks.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-text mb-3">Clips ({blocks.length} blocks)</h2>
          <div className="space-y-4">
            {blocks.map((block, bi) => (
              <div key={block.id} className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-text-muted uppercase">{block.type}</span>
                    <span className="text-[10px] text-text-muted">Block {bi + 1}</span>
                    {block.mood && (
                      <span className="text-[10px] text-text-muted italic">{block.mood}</span>
                    )}
                  </div>
                  {block.variants && block.variants.length > 0 && (
                    <span className="text-[10px] text-text-muted">
                      {block.variants.filter((v) => v.status?.toUpperCase() === "READY").length}/{block.variants.length} ready
                    </span>
                  )}
                </div>
                {block.variants && block.variants.length > 0 ? (
                  <div className="grid grid-cols-1 gap-3">
                    {block.variants.map((variant) => (
                      <VariantCard key={variant.id} variant={variant} />
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-text-muted">No variants generated yet</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* History */}
      {castId && <CastHistorySection castId={castId} />}

      {/* Bottom action bar for ready casts */}
      {isReady && (
        <div className="sticky bottom-4 flex justify-center">
          <Button
            size="lg"
            onClick={() => navigate("/live-control")}
            className="shadow-lg"
          >
            <Play className="mr-2 h-5 w-5" /> Go Live with this Cast
          </Button>
        </div>
      )}
    </div>
  );
}


function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    if (diff < 60_000) return "just now";
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}


function HistoryRow({ event }: { event: HistoryEvent }) {
  const [open, setOpen] = useState(false);
  const hasDiff = !!(event.before || event.after);
  const userLabel = event.user_id || "system";
  const sourceLabel = (event.metadata as any)?.source === "middleware_fallback" ? "fallback" : "web";

  return (
    <div className="rounded border border-border bg-card">
      <button
        type="button"
        onClick={() => hasDiff && setOpen((v) => !v)}
        className={cn(
          "w-full flex items-center gap-3 px-3 py-2 text-left",
          hasDiff ? "cursor-pointer hover:bg-surface" : "cursor-default",
        )}
      >
        {hasDiff ? (
          open ? <ChevronDown className="h-3 w-3 text-text-muted shrink-0" />
               : <ChevronRight className="h-3 w-3 text-text-muted shrink-0" />
        ) : (
          <span className="h-3 w-3 shrink-0" />
        )}
        <span
          className="text-[10px] text-text-muted shrink-0 font-mono"
          title={event.created_at || ""}
        >
          {formatRelative(event.created_at)}
        </span>
        <span className="text-xs text-text shrink-0 font-medium">{event.action}</span>
        <span className="text-[10px] text-text-muted">{event.entity_type}</span>
        {event.entity_id && (
          <span className="text-[10px] text-text-muted truncate font-mono">{event.entity_id}</span>
        )}
        <span className="ml-auto flex items-center gap-2">
          <span className="text-[10px] text-text-muted">{userLabel}</span>
          <Badge variant="outline" className="text-[9px]">{sourceLabel}</Badge>
        </span>
      </button>
      {open && hasDiff && (
        <div className="border-t border-border bg-surface px-3 py-2 grid grid-cols-2 gap-2 text-[10px]">
          <div>
            <p className="text-text-muted mb-1">before</p>
            <pre className="whitespace-pre-wrap break-all text-text-dim">
              {event.before ? JSON.stringify(event.before, null, 2) : "—"}
            </pre>
          </div>
          <div>
            <p className="text-text-muted mb-1">after</p>
            <pre className="whitespace-pre-wrap break-all text-text-dim">
              {event.after ? JSON.stringify(event.after, null, 2) : "—"}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}


function CastHistorySection({ castId }: { castId: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["cast-history", castId],
    queryFn: () => historyApi.forCast(castId, { limit: 100 }),
    enabled: !!castId,
    staleTime: 30_000,
  });

  return (
    <div>
      <h2 className="text-sm font-semibold text-text mb-3">History</h2>
      {isLoading && (
        <div className="rounded border border-border bg-card p-4 text-center">
          <Loader2 className="mx-auto h-4 w-4 animate-spin text-accent" />
        </div>
      )}
      {isError && (
        <div className="rounded border border-border bg-card p-4 text-center text-xs text-text-dim">
          Could not load history.
        </div>
      )}
      {!isLoading && !isError && (data?.events?.length || 0) === 0 && (
        <div className="rounded border border-border bg-card p-4 text-center text-xs text-text-dim">
          No actions recorded yet.
        </div>
      )}
      {!isLoading && !isError && (data?.events?.length || 0) > 0 && (
        <div className="space-y-1">
          {data!.events.map((e) => (
            <HistoryRow key={e.id} event={e} />
          ))}
          {data?.next_cursor && (
            <p className="text-[10px] text-text-muted text-center pt-2">
              Showing latest 100 events.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
