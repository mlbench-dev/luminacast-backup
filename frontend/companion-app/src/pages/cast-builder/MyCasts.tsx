import { useEffect, useState } from "react";
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
  ChevronRight,
  User,
  ShoppingBag,
  Send,
  Trash2,
  Pencil,
  Check,
  X,
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
import { useAuthStore } from "@/stores/authStore";
import { TeamRole } from "@/lib/types";

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
  // Cosmetic gate — routers/casts.py's require_role(CREATOR) on create_cast
  // enforces this independently regardless of what's shown here.
  const canCreate = useAuthStore((s) => s.hasTeamRole(TeamRole.CREATOR));
  const qc = useQueryClient();

  const PER_PAGE = 10;
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ["casts", page],
    queryFn: () => castsApi.list({ page, per_page: PER_PAGE }),
    refetchInterval: 5000,
  });

  const casts = (data as any)?.casts || [];
  const total: number = (data as any)?.total ?? casts.length;
  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  // Deleting the last item(s) on a page (e.g. the entire last page via
  // batch delete) can leave `page` past the new end — step back rather
  // than showing a blank "no casts" state while casts still exist earlier.
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  // Batch selection — persists across pages (selecting on page 1, then
  // paging to 2 and selecting more, deletes both sets together).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => castsApi.batchDelete(ids),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["casts"] });
      setSelectedIds(new Set());
      const failedCount = Object.keys(res.failed || {}).length;
      if (failedCount > 0) {
        toast({
          title: `Deleted ${res.deleted.length}, ${failedCount} failed`,
          description: Object.values(res.failed).join(", "),
          variant: "destructive",
        });
      } else {
        toast({ title: `Deleted ${res.deleted.length} cast${res.deleted.length !== 1 ? "s" : ""}` });
      }
    },
    onError: (err: any) => {
      toast({
        title: "Batch delete failed",
        description: err?.response?.data?.detail || err?.message,
        variant: "destructive",
      });
    },
  });

  // Inline rename — lets a cast be renamed directly from the list instead
  // of only inside the builder's Setup step.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const renameMutation = useMutation({
    mutationFn: ({ castId, name }: { castId: string; name: string }) =>
      castsApi.patch(castId, { name: name || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["casts"] });
    },
    onError: (err: any) => {
      toast({
        title: "Could not rename cast",
        description: err?.response?.data?.detail || err?.message,
        variant: "destructive",
      });
    },
  });

  const commitRename = (castId: string) => {
    const trimmed = renameValue.trim();
    setRenamingId(null);
    renameMutation.mutate({ castId, name: trimmed });
  };

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
          <p className="text-sm text-text-dim">{total} cast{total !== 1 ? "s" : ""}</p>
        </div>
        {canCreate && (
          <Button onClick={() => navigate("/cast-builder/new")} className="gap-2 cursor-pointer">
            <Plus className="h-4 w-4" /> New Cast
          </Button>
        )}
      </div>

      {selectedIds.size > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5">
          <span className="text-sm text-text">{selectedIds.size} selected</span>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
              Clear
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={batchDeleteMutation.isPending}
              onClick={async () => {
                const confirmed = await confirmAction({
                  title: `Delete ${selectedIds.size} cast${selectedIds.size !== 1 ? "s" : ""}?`,
                  text: "This will permanently remove the selected casts and all their blocks, renders, and publish records. This cannot be undone.",
                  confirmButtonText: "Delete",
                  cancelButtonText: "Cancel",
                  icon: "warning",
                });
                if (!confirmed) return;
                batchDeleteMutation.mutate([...selectedIds]);
              }}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" /> Delete selected
            </Button>
          </div>
        </div>
      )}

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
          {canCreate && (
            <Button onClick={() => navigate("/cast-builder/new")} className="mt-4">
              <Plus className="mr-1 h-4 w-4" /> Create Cast
            </Button>
          )}
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
                {/* Selection checkbox — sits outside the navigate button
                    (nesting an input inside a button is invalid HTML and
                    behaves inconsistently), absolutely positioned to match
                    the delete icon's pattern on the opposite corner. */}
                <input
                  type="checkbox"
                  checked={selectedIds.has(cast.id)}
                  onChange={() => toggleSelected(cast.id)}
                  onClick={(e) => e.stopPropagation()}
                  className="absolute top-4 left-4 z-10 h-4 w-4 rounded border-border accent-accent cursor-pointer"
                  aria-label="Select cast"
                  data-testid={`cast-card-${cast.id}-select`}
                />
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
                {/* pl-6/pr-9 reserve clear space in the top corners for the
                    absolutely-positioned checkbox/delete button below, so
                    neither overlaps the row content that would otherwise
                    flow all the way to the row's edges. */}
                <div className="flex items-center gap-4 pl-6 pr-9">
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
                      {renamingId === cast.id ? (
                        <div
                          className="flex items-center gap-1 flex-1 min-w-0"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            autoFocus
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") commitRename(cast.id);
                              if (e.key === "Escape") setRenamingId(null);
                            }}
                            onBlur={() => commitRename(cast.id)}
                            placeholder="Untitled Cast"
                            className="flex-1 min-w-0 bg-white/5 border border-accent/40 rounded px-1.5 py-0.5 text-sm font-medium text-text focus:outline-none"
                          />
                          <button
                            type="button"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => commitRename(cast.id)}
                            className="p-1 text-green-400 hover:text-green-300 shrink-0"
                            aria-label="Save name"
                          >
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => setRenamingId(null)}
                            className="p-1 text-white/40 hover:text-white/70 shrink-0"
                            aria-label="Cancel rename"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ) : (
                        <>
                          <p className="font-medium text-text truncate">{cast.name || "Untitled Cast"}</p>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setRenameValue(cast.name || "");
                              setRenamingId(cast.id);
                            }}
                            className="p-0.5 text-white/25 opacity-0 group-hover:opacity-100 hover:text-white/60 transition-opacity shrink-0"
                            aria-label="Rename cast"
                            title="Rename cast"
                          >
                            <Pencil className="w-3 h-3" />
                          </button>
                        </>
                      )}
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

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="h-4 w-4 mr-1" /> Previous
          </Button>
          <span className="text-sm text-text-dim">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Next <ChevronRight className="h-4 w-4 ml-1" />
          </Button>
        </div>
      )}
    </div>
  );
}
