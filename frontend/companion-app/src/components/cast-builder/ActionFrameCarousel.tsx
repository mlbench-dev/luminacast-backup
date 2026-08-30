import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Plus, Sparkles, Trash2, X, ZoomIn } from "lucide-react";
import { castsApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";

type Frame = {
  id: string;
  image_url: string | null;
  status: string;
  error_message: string | null;
  background_prompt: string | null;
  kind: "start" | "end";
  created_at: string | null;
};

interface ActionFrameCarouselProps {
  castId: string;
  blockId: string;
  startLookId: string | null;
  endLookId: string | null;
  startPromptSeed: string | null;
  endPromptSeed: string | null;
  productName?: string | null;
  onSelectionChange: (kind: "start" | "end", lookId: string) => Promise<void> | void;
}

/**
 * Carousel of AI-generated SCENE frames for an avatar_action block.
 *
 * Mirrors BodyMotionFrameCarousel but reads from /action_frames and writes
 * via /action_frame so the backend can prepend the avatar's appearance and
 * key the resulting AvatarLook rows under `action_block_<id>_<kind>`.
 *
 * Each kind (start/end) gets its own row of thumbnails. Click a thumbnail
 * to pin it as the selected frame for the block (saves to the block via
 * body_motion_start_look_id / body_motion_end_look_id — the column is reused
 * across both pipelines). Click the magnifier (or double-click the
 * thumbnail) to open a zoom modal where the user can edit the prompt and
 * regenerate. The "+" tile opens the same modal with the writer's seed
 * prompt prefilled, ready to author a fresh frame.
 */
export function ActionFrameCarousel({
  castId,
  blockId,
  startLookId,
  endLookId,
  startPromptSeed,
  endPromptSeed,
  productName,
  onSelectionChange,
}: ActionFrameCarouselProps) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => ["action-frames", castId, blockId],
    [castId, blockId],
  );

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => castsApi.listActionFrames(castId, blockId),
    enabled: !!castId && !!blockId,
    refetchInterval: (q) => {
      const d = q.state.data as
        | { frames?: { start: Frame[]; end: Frame[] } }
        | undefined;
      if (!d?.frames) return 4000;
      const pending = [...d.frames.start, ...d.frames.end].some(
        (f) => f.status === "pending" || f.status === "generating",
      );
      return pending ? 4000 : false;
    },
  });

  const [zoom, setZoom] = useState<{
    kind: "start" | "end";
    look: Frame | null;
    seedPrompt: string;
  } | null>(null);

  // Optimistic pending selection per kind: shows the click as selected
  // immediately while the PATCH is in flight. Cleared once the parent
  // prop (driven by the API response) catches up, or on error/revert.
  const [pendingSelect, setPendingSelect] = useState<{
    start: string | null;
    end: string | null;
  }>({ start: null, end: null });

  useEffect(() => {
    if (pendingSelect.start && pendingSelect.start === startLookId) {
      setPendingSelect((p) => ({ ...p, start: null }));
    }
  }, [startLookId, pendingSelect.start]);

  useEffect(() => {
    if (pendingSelect.end && pendingSelect.end === endLookId) {
      setPendingSelect((p) => ({ ...p, end: null }));
    }
  }, [endLookId, pendingSelect.end]);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey });
  }, [queryClient, queryKey]);

  const handleDeleteFrame = useCallback(
    async (frameId: string) => {
      const ok = await confirmAction({
        title: "Delete this frame?",
        text: "The generated frame is removed from the carousel. This can't be undone.",
        confirmButtonText: "Delete",
        cancelButtonText: "Cancel",
        icon: "warning",
      });
      if (!ok) return;
      try {
        await castsApi.deleteActionFrame(castId, blockId, frameId);
        toast({ title: "Frame deleted" });
        refresh();
      } catch (err: any) {
        toast({
          title: "Couldn't delete frame",
          description: err?.response?.data?.detail || err?.message || "",
          variant: "destructive",
        });
      }
    },
    [castId, blockId, refresh],
  );

  const handlePin = useCallback(
    async (kind: "start" | "end", lookId: string) => {
      setPendingSelect((p) => ({ ...p, [kind]: lookId }));
      try {
        await onSelectionChange(kind, lookId);
      } catch (err: any) {
        setPendingSelect((p) => ({ ...p, [kind]: null }));
        toast({
          title: "Couldn't pin frame",
          description: err?.response?.data?.detail || err?.message || "",
          variant: "destructive",
        });
      }
    },
    [onSelectionChange],
  );

  const renderRow = (kind: "start" | "end", title: string) => {
    const frames = data?.frames?.[kind] || [];
    const seed = (kind === "start" ? startPromptSeed : endPromptSeed) || "";
    const propSelectedId = kind === "start" ? startLookId : endLookId;
    const pending = pendingSelect[kind];
    const selectedId = pending || propSelectedId;

    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-[10px] text-white/40 uppercase tracking-wider">
            {title}
          </label>
          {frames.length === 0 && !isLoading && seed && (
            <button
              type="button"
              onClick={() => setZoom({ kind, look: null, seedPrompt: seed })}
              className="text-[10px] text-orange-300 hover:text-orange-200 inline-flex items-center gap-1"
              data-testid={`af-generate-now-${kind}`}
            >
              <Sparkles className="w-3 h-3" /> Generate now
            </button>
          )}
        </div>
        <div className="flex gap-2 overflow-x-auto pb-1">
          {isLoading && frames.length === 0 ? (
            <FrameTile kind={kind} state="loading" />
          ) : frames.length === 0 ? (
            <FrameTile
              kind={kind}
              state="empty"
              hint={seed ? "Generating..." : "Edit the script to seed a frame"}
            />
          ) : (
            frames.map((frame) => (
              <FrameTile
                key={frame.id}
                kind={kind}
                state={frame.status as any}
                imageUrl={frame.image_url}
                selected={frame.id === selectedId}
                onSelect={async () => {
                  if (frame.status !== "ready") return;
                  if (frame.id === selectedId) return;
                  await handlePin(kind, frame.id);
                }}
                onZoom={() =>
                  setZoom({
                    kind,
                    look: frame,
                    seedPrompt: frame.background_prompt || seed,
                  })
                }
                onDelete={() => handleDeleteFrame(frame.id)}
                error={frame.error_message}
              />
            ))
          )}
          <AddTile
            onClick={() =>
              setZoom({ kind, look: null, seedPrompt: seed })
            }
          />
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="space-y-2.5">
        {renderRow("start", "Start Frame")}
        {renderRow("end", "End Frame")}
      </div>
      {zoom && (
        <FrameZoomModal
          castId={castId}
          blockId={blockId}
          kind={zoom.kind}
          look={zoom.look}
          seedPrompt={zoom.seedPrompt}
          productName={productName}
          onClose={() => setZoom(null)}
          onGenerated={() => {
            refresh();
          }}
        />
      )}
    </>
  );
}

function FrameTile({
  kind,
  state,
  imageUrl,
  selected,
  onSelect,
  onZoom,
  onDelete,
  error,
  hint,
}: {
  kind: "start" | "end";
  state: "loading" | "empty" | "pending" | "generating" | "ready" | "failed";
  imageUrl?: string | null;
  selected?: boolean;
  onSelect?: () => void;
  onZoom?: () => void;
  onDelete?: () => void;
  error?: string | null;
  hint?: string;
}) {
  const ringClass = selected
    ? "ring-2 ring-orange-500"
    : "ring-1 ring-white/10 hover:ring-white/30";

  const baseClass = `relative w-20 h-32 shrink-0 rounded-md bg-zinc-800 overflow-hidden ${ringClass}`;

  if (state === "loading" || state === "pending" || state === "generating") {
    return (
      <div
        className={baseClass}
        data-testid={`af-tile-${kind}-loading`}
      >
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 className="w-5 h-5 animate-spin text-white/60" />
        </div>
      </div>
    );
  }

  if (state === "failed") {
    return (
      <div
        className={`${baseClass} group border border-red-500/40`}
        title={error || "Generation failed"}
        data-testid={`af-tile-${kind}-failed`}
      >
        <div className="absolute inset-0 flex flex-col items-center justify-center text-[10px] text-red-300 px-1 text-center">
          <X className="w-4 h-4 mb-1" />
          Failed
        </div>
        {onDelete && (
          <div
            className="absolute top-1 right-1 p-1 rounded bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            role="button"
            aria-label="Delete frame"
          >
            <Trash2 className="w-3 h-3 text-red-300" />
          </div>
        )}
      </div>
    );
  }

  if (state === "empty") {
    return (
      <div
        className={baseClass}
        data-testid={`af-tile-${kind}-empty`}
      >
        <div className="absolute inset-0 flex items-center justify-center text-[10px] text-white/40 px-2 text-center">
          {hint || "No frames yet"}
        </div>
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`${baseClass} group`}
      onClick={onSelect}
      onDoubleClick={onZoom}
      aria-pressed={selected || undefined}
      data-testid={`af-tile-${kind}${selected ? "-selected" : ""}`}
    >
      {imageUrl ? (
        <img
          src={imageUrl}
          alt="frame"
          className="absolute inset-0 w-full h-full object-cover"
        />
      ) : null}
      {selected && (
        <div
          className="absolute top-1 left-1 w-5 h-5 rounded-full bg-orange-500 text-white flex items-center justify-center shadow-md"
          aria-label="Pinned"
          data-testid={`af-tile-${kind}-check`}
        >
          <Check className="w-3 h-3" strokeWidth={3} />
        </div>
      )}
      <div className="absolute top-1 right-1 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <div
          className="p-1 rounded bg-black/50"
          onClick={(e) => {
            e.stopPropagation();
            onZoom?.();
          }}
          role="button"
          aria-label="Zoom"
        >
          <ZoomIn className="w-3 h-3 text-white" />
        </div>
        {onDelete && (
          <div
            className="p-1 rounded bg-black/50 hover:bg-red-600/70"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            role="button"
            aria-label="Delete frame"
          >
            <Trash2 className="w-3 h-3 text-white" />
          </div>
        )}
      </div>
    </button>
  );
}

function AddTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-20 h-32 shrink-0 rounded-md border border-dashed border-white/20 hover:border-orange-400 text-white/40 hover:text-orange-300 flex flex-col items-center justify-center gap-1"
      data-testid="af-add-tile"
    >
      <Plus className="w-4 h-4" />
      <span className="text-[10px]">Add Frame</span>
    </button>
  );
}

function FrameZoomModal({
  castId,
  blockId,
  kind,
  look,
  seedPrompt,
  productName,
  onClose,
  onGenerated,
}: {
  castId: string;
  blockId: string;
  kind: "start" | "end";
  look: Frame | null;
  seedPrompt: string;
  productName?: string | null;
  onClose: () => void;
  onGenerated: () => void;
}) {
  const [prompt, setPrompt] = useState(
    look?.background_prompt || seedPrompt || "",
  );
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = useCallback(async () => {
    if (!look) return;
    const ok = await confirmAction({
      title: "Delete this frame?",
      text: "The generated frame is removed from the carousel. This can't be undone.",
      confirmButtonText: "Delete",
      cancelButtonText: "Cancel",
      icon: "warning",
    });
    if (!ok) return;
    setDeleting(true);
    try {
      await castsApi.deleteActionFrame(castId, blockId, look.id);
      toast({ title: "Frame deleted" });
      onGenerated();
      onClose();
    } catch (err: any) {
      toast({
        title: "Couldn't delete frame",
        description: err?.response?.data?.detail || err?.message || "",
        variant: "destructive",
      });
    } finally {
      setDeleting(false);
    }
  }, [look, castId, blockId, onGenerated, onClose]);

  useEffect(() => {
    setPrompt(look?.background_prompt || seedPrompt || "");
  }, [look, seedPrompt]);

  const handleGenerate = useCallback(async () => {
    const trimmed = prompt.trim();
    if (!trimmed) {
      toast({
        title: "Prompt required",
        description: "Describe the frame before generating.",
        variant: "destructive",
      });
      return;
    }
    setSubmitting(true);
    try {
      await castsApi.generateActionFrame(castId, blockId, {
        kind,
        prompt: trimmed,
        regenerate: !!look,
      });
      toast({ title: `New ${kind} frame queued for AI render` });
      onGenerated();
      onClose();
    } catch (err: any) {
      toast({
        title: "Failed to start AI render",
        description: err?.response?.data?.detail || err?.message || "",
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  }, [prompt, castId, blockId, kind, look, onGenerated, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-white/10 rounded-lg max-w-md w-full p-4 space-y-3 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        data-testid={`af-zoom-${kind}`}
      >
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium text-white/90">
            {look ? "Edit & Regenerate" : "New"} {kind === "start" ? "Start" : "End"} Frame
          </h4>
          <button
            type="button"
            onClick={onClose}
            className="text-white/50 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        {look?.image_url ? (
          <div className="rounded bg-zinc-800 overflow-hidden flex items-center justify-center">
            <img
              src={look.image_url}
              alt="frame"
              className="max-h-[45vh] w-auto max-w-full object-contain"
            />
          </div>
        ) : (
          <div className="rounded bg-zinc-800 h-24 flex items-center justify-center text-white/30 text-xs text-center px-4">
            New frame preview will appear after AI render finishes.
          </div>
        )}
        <div>
          <label className="text-[10px] text-white/40 uppercase tracking-wider mb-1 block">
            Scene description (avatar appearance is auto-prepended)
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
            placeholder="Describe the scene: setting, camera angle, pose, expression, lighting..."
            className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm text-white/90 placeholder:text-white/30 focus:outline-hidden focus:border-orange-500/50"
            data-testid={`af-prompt-${kind}`}
          />
          {productName ? (
            <p
              className="mt-1 text-[10px] text-white/40"
              data-testid={`af-product-tip-${kind}`}
            >
              Tip: mention {productName} or "holding the bottle" to reference your product image.
            </p>
          ) : null}
        </div>
        <div className="flex items-center justify-between gap-2">
          <div>
            {look && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting || submitting}
                className="px-2 py-1.5 text-xs text-red-400 hover:text-red-300 inline-flex items-center gap-1 disabled:opacity-50"
                data-testid={`af-delete-${kind}`}
              >
                {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                Delete
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-white/70 hover:text-white"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={submitting}
            className="px-3 py-1.5 text-xs rounded bg-orange-500 hover:bg-orange-400 text-white inline-flex items-center gap-1 disabled:opacity-50"
            data-testid={`af-regenerate-${kind}`}
          >
            {submitting ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Sparkles className="w-3 h-3" />
            )}
            {look ? "Regenerate" : "Generate"}
          </button>
          </div>
        </div>
      </div>
    </div>
  );
}
