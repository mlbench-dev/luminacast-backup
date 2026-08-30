import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Plus, Sparkles, X, ZoomIn } from "lucide-react";
import { castsApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";

type Frame = {
  id: string;
  image_url: string | null;
  status: string;
  error_message: string | null;
  background_prompt: string | null;
  kind: "start" | "end";
  created_at: string | null;
};

interface BodyMotionFrameCarouselProps {
  castId: string;
  blockId: string;
  startLookId: string | null;
  endLookId: string | null;
  startPromptSeed: string | null;
  endPromptSeed: string | null;
  onSelectionChange: (kind: "start" | "end", lookId: string) => Promise<void> | void;
}

/**
 * Carousel of AI-generated start/end frames for a body-motion block.
 *
 * Each kind (start/end) gets its own row of thumbnails. Click a thumbnail
 * to pin it as the selected frame for the block (saves to the block via
 * body_motion_start_look_id / body_motion_end_look_id). Click the magnifier
 * (or double-click the thumbnail) to open a zoom modal where the user can
 * edit the prompt and regenerate. The "+" tile opens the same modal with
 * the script-writer's seed prompt prefilled, ready to author a fresh frame.
 */
export function BodyMotionFrameCarousel({
  castId,
  blockId,
  startLookId,
  endLookId,
  startPromptSeed,
  endPromptSeed,
  onSelectionChange,
}: BodyMotionFrameCarouselProps) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => ["body-motion-frames", castId, blockId],
    [castId, blockId],
  );

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => castsApi.listBodyMotionFrames(castId, blockId),
    enabled: !!castId && !!blockId,
    // Light polling so a freshly-generated frame appears without a manual
    // reload. Stops once nothing is in flight (handled by `refetchInterval`
    // returning false when no pending rows remain).
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
              className="text-[10px] text-purple-300 hover:text-purple-200 inline-flex items-center gap-1"
              data-testid={`bmf-generate-now-${kind}`}
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
  error,
  hint,
}: {
  kind: "start" | "end";
  state: "loading" | "empty" | "pending" | "generating" | "ready" | "failed";
  imageUrl?: string | null;
  selected?: boolean;
  onSelect?: () => void;
  onZoom?: () => void;
  error?: string | null;
  hint?: string;
}) {
  const ringClass = selected
    ? "ring-2 ring-purple-500"
    : "ring-1 ring-white/10 hover:ring-white/30";

  const baseClass = `relative w-20 h-32 shrink-0 rounded-md bg-zinc-800 overflow-hidden ${ringClass}`;

  if (state === "loading" || state === "pending" || state === "generating") {
    return (
      <div
        className={baseClass}
        data-testid={`bmf-tile-${kind}-loading`}
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
        className={`${baseClass} border border-red-500/40`}
        title={error || "Generation failed"}
        data-testid={`bmf-tile-${kind}-failed`}
      >
        <div className="absolute inset-0 flex flex-col items-center justify-center text-[10px] text-red-300 px-1 text-center">
          <X className="w-4 h-4 mb-1" />
          Failed
        </div>
      </div>
    );
  }

  if (state === "empty") {
    return (
      <div
        className={baseClass}
        data-testid={`bmf-tile-${kind}-empty`}
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
      data-testid={`bmf-tile-${kind}${selected ? "-selected" : ""}`}
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
          className="absolute top-1 left-1 w-5 h-5 rounded-full bg-purple-500 text-white flex items-center justify-center shadow-md"
          aria-label="Pinned"
          data-testid={`bmf-tile-${kind}-check`}
        >
          <Check className="w-3 h-3" strokeWidth={3} />
        </div>
      )}
      <div
        className="absolute top-1 right-1 p-1 rounded bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity"
        onClick={(e) => {
          e.stopPropagation();
          onZoom?.();
        }}
        role="button"
        aria-label="Zoom"
      >
        <ZoomIn className="w-3 h-3 text-white" />
      </div>
    </button>
  );
}

function AddTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-20 h-32 shrink-0 rounded-md border border-dashed border-white/20 hover:border-purple-400 text-white/40 hover:text-purple-300 flex flex-col items-center justify-center gap-1"
      data-testid="bmf-add-tile"
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
  onClose,
  onGenerated,
}: {
  castId: string;
  blockId: string;
  kind: "start" | "end";
  look: Frame | null;
  seedPrompt: string;
  onClose: () => void;
  onGenerated: () => void;
}) {
  const [prompt, setPrompt] = useState(
    look?.background_prompt || seedPrompt || "",
  );
  const [submitting, setSubmitting] = useState(false);

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
      await castsApi.generateBodyMotionFrame(castId, blockId, {
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
        data-testid={`bmf-zoom-${kind}`}
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
            Prompt
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
            placeholder="Describe the frame: camera angle, pose, expression, scene..."
            className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm text-white/90 placeholder:text-white/30 focus:outline-hidden focus:border-purple-500/50"
            data-testid={`bmf-prompt-${kind}`}
          />
        </div>
        <div className="flex justify-end gap-2">
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
            className="px-3 py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-500 text-white inline-flex items-center gap-1 disabled:opacity-50"
            data-testid={`bmf-regenerate-${kind}`}
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
  );
}
