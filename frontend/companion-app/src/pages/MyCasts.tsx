import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Film,
  Loader2,
  CheckCircle2,
  Clock,
  AlertCircle,
  Play,
  ChevronLeft,
  User,
  ShoppingBag,
  Send,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { confirmAction } from "@/lib/swal";
import { castsApi, queryClient } from "@/lib/api";
import { CastStatus } from "@/lib/types";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { formatCents } from "@/lib/billing";
import { toast } from "@/hooks/useToast";

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: typeof Clock }> = {
  draft: { label: "Draft", color: "bg-gray-500/10 text-gray-400", icon: Clock },
  outline_review: { label: "Outline", color: "bg-blue-500/10 text-blue-400", icon: Clock },
  script_review: { label: "Scripts", color: "bg-blue-500/10 text-blue-400", icon: Clock },
  template_select: { label: "Layout", color: "bg-blue-500/10 text-blue-400", icon: Clock },
  pending_payment: { label: "Payment", color: "bg-yellow-500/10 text-yellow-400", icon: Clock },
  generating_tts: { label: "Generating Audio", color: "bg-accent/10 text-accent", icon: Loader2 },
  tts_ready: { label: "Audio Ready", color: "bg-blue-500/10 text-blue-400", icon: CheckCircle2 },
  generating_videos: { label: "Rendering", color: "bg-accent/10 text-accent", icon: Loader2 },
  generating: { label: "Generating", color: "bg-accent/10 text-accent", icon: Loader2 },
  generation_failed: { label: "Failed", color: "bg-red-500/10 text-red-400", icon: AlertCircle },
  ready: { label: "Ready", color: "bg-green-500/10 text-green-400", icon: CheckCircle2 },
  scheduled: { label: "Scheduled", color: "bg-purple-500/10 text-purple-400", icon: Clock },
  live: { label: "Live", color: "bg-red-500/10 text-red-400", icon: Play },
  completed: { label: "Completed", color: "bg-gray-500/10 text-gray-400", icon: CheckCircle2 },
};

const QUALITY_LABELS: Record<string, { label: string; color: string }> = {
  simple: { label: "Simple", color: "bg-white/10 text-white/50" },
  hd: { label: "HD", color: "bg-blue-500/20 text-blue-300" },
  hd_plus: { label: "HD+", color: "bg-purple-500/20 text-purple-300" },
};

function formatDuration(blocks: any[] | undefined): string {
  if (!blocks || blocks.length === 0) return "";
  const total = blocks.reduce((sum: number, b: any) => sum + (b.duration_seconds || 0), 0);
  if (total <= 0) return "";
  const m = Math.floor(total / 60);
  const s = Math.round(total % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function MyCastsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["casts"],
    queryFn: () => castsApi.list(),
    refetchInterval: 5000,
  });

  const casts = (data as any)?.casts || [];

  const retryMutation = useMutation({
    mutationFn: (castId: string) => castsApi.retry(castId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["casts"] });
      toast({ title: "Retrying failed clips..." });
    },
    onError: (err: any) => {
      toast({ title: "Retry failed", description: err?.response?.data?.detail, variant: "destructive" });
    },
  });

  // Optimistic delete: remove the cast from the list immediately, then
  // roll back on error. The server returns 204 on success.
  const deleteMutation = useMutation({
    mutationFn: (castId: string) => castsApi.delete(castId),
    onMutate: async (castId: string) => {
      await qc.cancelQueries({ queryKey: ["casts"] });
      const previous = qc.getQueryData<any>(["casts"]);
      if (previous?.casts) {
        qc.setQueryData(["casts"], {
          ...previous,
          casts: previous.casts.filter((c: any) => c.id !== castId),
        });
      }
      return { previous };
    },
    onError: (err: any, _castId, context) => {
      if (context?.previous) qc.setQueryData(["casts"], context.previous);
      toast({
        title: "Could not delete cast",
        description: err?.response?.data?.detail || err?.message,
        variant: "destructive",
      });
    },
    onSuccess: () => {
      toast({ title: "Cast deleted" });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["casts"] });
    },
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6" data-testid="my-casts-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">My Casts</h1>
          <p className="text-sm text-text-dim">{casts.length} cast{casts.length !== 1 ? "s" : ""}</p>
        </div>
        <Button onClick={() => navigate("/cast-builder/new")} className="gap-2 cursor-pointer">
          <Plus className="h-4 w-4" /> New Cast
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg bg-card" />
          ))}
        </div>
      ) : casts.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border py-12 text-center">
          <Film className="mx-auto mb-3 h-10 w-10 text-text-muted" />
          <h3 className="text-lg font-semibold text-text">No casts yet</h3>
          <p className="mt-1 text-sm text-text-dim">Create your first AI-powered live selling cast</p>
          <Button onClick={() => navigate("/cast-builder/new")} className="mt-4">
            <Plus className="mr-1 h-4 w-4" /> Create Cast
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {casts.map((cast: any) => {
            const config = STATUS_CONFIG[cast.status?.toLowerCase()] || STATUS_CONFIG[cast.status] || STATUS_CONFIG.draft;
            const StatusIcon = config.icon;
            const isGenerating = cast.status === CastStatus.GENERATING;
            const isRendering = ["generating_videos", "generating"].includes(cast.status?.toLowerCase());
            const qualityInfo = QUALITY_LABELS[cast.quality] || QUALITY_LABELS.simple;
            const duration = formatDuration(cast.blocks);
            // Avatar thumbnail: prefer the resolved URL from the API
            // (default-look face → avatar face fallback), fall back to
            // the raw face_ref_key on legacy responses.
            const avatarThumbUrl: string | null =
              cast.avatar_thumbnail_url ||
              (cast.avatar?.face_ref_key ? cdnUrl(cast.avatar.face_ref_key) : null);
            const avatarName: string = cast.avatar_name || cast.avatar?.name || "Avatar";
            const firstProduct = cast.products?.[0];
            const firstProductImage = firstProduct?.images?.[0]?.r2_key || firstProduct?.image_r2_key;

            return (
              <div
                key={cast.id}
                className="group relative w-full rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-accent/30"
                data-testid={`cast-card-${cast.id}`}
              >
                <button
                  onClick={() => {
                    // If rendering, go directly to editor (render progress shows inline)
                    if (isRendering) {
                      navigate(`/cast-builder/${cast.id}/editor`);
                    } else {
                      navigate(`/cast-builder/${cast.id}`);
                    }
                  }}
                  className="w-full text-left cursor-pointer focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 rounded-lg"
                >
                {/* pr-9 reserves clear space in the top-right corner for the
                    absolutely-positioned delete button below, so it never
                    overlaps the status badge that would otherwise flow all
                    the way to the row's edge. */}
                <div className="flex items-center gap-4 pr-9">
                  {/* Thumbnails: avatar face + product */}
                  <div className="flex items-center gap-2 shrink-0">
                    {/* Avatar face thumbnail (36px circle) — sourced from
                        the cast's default avatar look (or avatar fallback)
                        so the user can tell at a glance which avatar is
                        acting in this cast. */}
                    <div
                      className="w-9 h-9 rounded-full bg-white/5 ring-1 ring-white/15 overflow-hidden flex items-center justify-center shrink-0"
                      title={avatarName}
                    >
                      {avatarThumbUrl ? (
                        <img src={avatarThumbUrl} alt={avatarName} className="w-full h-full object-cover" />
                      ) : (
                        <User className="w-4 h-4 text-text-muted" />
                      )}
                    </div>
                    {/* Product thumbnail (32px) */}
                    {firstProductImage && (
                      <div className="w-8 h-8 rounded bg-white/5 border border-white/10 overflow-hidden flex items-center justify-center shrink-0">
                        <img src={cdnUrl(firstProductImage)} alt="" className="w-full h-full object-cover" />
                      </div>
                    )}
                  </div>

                  {/* Cast info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-text truncate">{cast.name || "Untitled Cast"}</p>
                      {cast.version != null && cast.version > 1 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-white/50 shrink-0">
                          v{cast.version}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5 text-xs text-text-muted">
                      <span>{new Date(cast.created_at).toLocaleDateString()}</span>
                      {duration && <><span>·</span><span>{duration}</span></>}
                      {cast.blocks?.length > 0 && <><span>·</span><span>{cast.blocks.length} block{cast.blocks.length !== 1 ? "s" : ""}</span></>}
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${qualityInfo.color}`}>
                        {qualityInfo.label}
                      </span>
                    </div>
                  </div>

                  {/* Status + render info */}
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    {cast.render_status === "ready" ? (
                      <div className="flex items-center gap-2">
                        <span className="flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium bg-green-500/10 text-green-400">
                          <CheckCircle2 className="h-3 w-3" />
                          Ready
                        </span>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2.5 text-xs"
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/publish/${cast.id}`);
                          }}
                        >
                          <Send className="h-3 w-3 mr-1" />
                          Publish
                        </Button>
                      </div>
                    ) : ["queued", "baking", "composing"].includes(cast.render_status) ? (
                      <span className="flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium bg-amber-500/10 text-amber-300">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Rendering {cast.render_progress_percent ? `${cast.render_progress_percent}%` : ""}
                      </span>
                    ) : cast.render_status === "failed" ? (
                      <span
                        className="flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium bg-red-500/10 text-red-400"
                        title={cast.render_error_message || "Rendering failed"}
                      >
                        <AlertCircle className="h-3 w-3" />
                        Failed
                      </span>
                    ) : (
                      <span className={cn("flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium", config.color)}>
                        <StatusIcon className={cn("h-3 w-3", isGenerating && "animate-spin")} />
                        {config.label}
                      </span>
                    )}
                  </div>
                </div>

                {cast.status?.toLowerCase() === "draft" && (
                  <span className="ml-14 text-[10px] text-accent font-medium">
                    Continue &rarr;
                  </span>
                )}
                {isGenerating && (
                  <div className="mt-3">
                    <Progress value={(cast.generation_progress || 0) * 100} className="h-1.5" />
                    <p className="mt-1 text-[10px] text-text-muted">
                      {Math.round((cast.generation_progress || 0) * 100)}% — {
                        (cast.generation_progress || 0) < 0.5
                          ? (cast as any).progress_step || "Generating speech audio..."
                          : "Rendering video on GPU..."
                      }
                    </p>
                    {(cast.generation_progress || 0) >= 0.5 && (
                      <p className="mt-0.5 text-[10px] text-text-muted">
                        You can close this page — rendering continues in the background.
                      </p>
                    )}
                  </div>
                )}
                </button>
                {cast.status === CastStatus.GENERATION_FAILED && (
                  <div className="mt-3">
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={(e) => {
                          e.stopPropagation();
                          retryMutation.mutate(cast.id);
                        }}
                      >
                        ↻ Retry
                      </Button>
                    </div>
                    {(cast as any).generation_error && (
                      <p className="mt-1 text-[10px] text-red-400 truncate">{(cast as any).generation_error}</p>
                    )}
                  </div>
                )}
                {/* Delete trash icon — revealed on row hover, mirrors the
                    pattern used elsewhere in the app (Publish hub
                    ScheduledPostCard). Stops propagation so it doesn't
                    navigate when the user clicks it. Red at all times (not
                    just on hover) so it reads clearly as destructive once
                    revealed — the row content reserves space via pr-10 on
                    its top row so this never overlaps the status badge. */}
                <button
                  type="button"
                  onClick={async (e) => {
                    e.stopPropagation();
                    const confirmed = await confirmAction({
                      title: "Delete this cast?",
                      text: `This will permanently remove the cast "${cast.name || "Untitled Cast"}" and all its blocks, renders, and publish records. This cannot be undone.`,
                      confirmButtonText: "Delete cast",
                      cancelButtonText: "Cancel",
                      icon: "warning",
                    });
                    if (!confirmed) return;
                    deleteMutation.mutate(cast.id);
                  }}
                  className="absolute top-3 right-3 p-1.5 rounded-md text-red-400 opacity-0 group-hover:opacity-100 hover:text-red-300 hover:bg-red-500/10 focus:opacity-100 transition-opacity"
                  aria-label="Delete cast"
                  data-testid={`cast-card-${cast.id}-delete`}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
