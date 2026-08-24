import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, AlertCircle, RefreshCw, ArrowLeft, Trash2, Rocket, MoreVertical, Copy, ChevronLeft, CheckCircle2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { castsApi } from "@/lib/api";
import { CastStatus, type Cast } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { PhaseHeader, type WizardPhase } from "@/components/cast-builder/PhaseHeader";
import { SetupPhase } from "@/components/cast-builder/SetupPhase";
import { ScriptGeneratingPhase } from "@/components/cast-builder/ScriptGeneratingPhase";
import { ScriptPhase } from "@/components/cast-builder/ScriptPhase";
import { AudioGeneratingPhase } from "@/components/cast-builder/AudioGeneratingPhase";
import { ArrangePhase, type ArrangePhaseHandle } from "@/components/cast-builder/ArrangePhase";
import { ReadyPhase } from "@/components/cast-builder/ReadyPhase";
import { VersionPicker } from "@/components/cast-builder/VersionPicker";
import { RenderStatusPill } from "@/components/cast-builder/RenderStatusPill";
import { RendersCollection, type RendersCollectionHandle } from "@/components/cast-builder/RendersCollection";

function statusToPhase(status: string): WizardPhase {
  switch (status?.toLowerCase()) {
    case "draft":
      return "script";
    case "outline_review":
    case "script_review":
      return "script";
    case "template_select":
    case "pending_payment":
      return "setup";
    case "generating_tts":
      return "audio_generating";
    case "tts_ready":
      return "editor";
    case "generating_videos":
    case "generating":
      return "editor";
    case "ready":
    case "scheduled":
    case "live":
    case "completed":
      return "ready";
    default:
      return "setup";
  }
}

const VALID_PHASES: WizardPhase[] = ["setup", "script", "audio_generating", "editor", "ready"];

export function CastBuilderPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { castId, phase: urlPhase } = useParams<{ castId?: string; phase?: string }>();
  const isNew = !castId;

  const [phase, setPhase] = useState<WizardPhase>(isNew ? "setup" : "setup");
  const [cast, setCast] = useState<Cast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const arrangePhaseRef = useRef<ArrangePhaseHandle>(null);
  const rendersRef = useRef<RendersCollectionHandle>(null);

  // Load existing cast
  const { data: loadedCast, isLoading } = useQuery({
    queryKey: ["cast", castId],
    queryFn: () => castsApi.get(castId!),
    enabled: !!castId,
  });

  // Active render check — stay on editor, render progress shows in button
  useEffect(() => {
    if (!loadedCast?.id) return;
    castsApi.listRenders(loadedCast.id).then((data: any) => {
      const rendersList = Array.isArray(data) ? data : [];
      const activeRender = rendersList.find((r: any) => ["queued", "baking", "composing"].includes(r.status));
      if (activeRender) {
        // Stay on editor — render progress is shown inline in the Finalize button
        setPhase("editor");
      }
    }).catch(() => { });
  }, [loadedCast?.id]);

  const initialLoadDoneRef = useRef(false);
  useEffect(() => {
    if (loadedCast) {
      // Only overwrite cast state from React Query on initial load or if version is newer
      setCast((prev) => {
        if (!prev) return loadedCast; // initial load
        if (loadedCast.version && loadedCast.version > (prev.version ?? 0)) return loadedCast; // genuine update
        if (!initialLoadDoneRef.current) {
          initialLoadDoneRef.current = true;
          // Merge rather than replace: if the GET response is ever missing a
          // field the just-created cast already had in memory (e.g. a field
          // not yet wired into the GET serializer), don't silently drop it.
          return { ...prev, ...loadedCast };
        }
        return prev; // don't overwrite with stale cached data
      });
      const inferred = statusToPhase(loadedCast.status);
      if (loadedCast.status === CastStatus.GENERATION_FAILED) {
        setError(loadedCast.generation_error || "Generation failed");
        if (loadedCast.progress_step?.toLowerCase().includes("tts") ||
          loadedCast.progress_step?.toLowerCase().includes("audio")) {
          setPhase("audio_generating");
        } else {
          setPhase("editor");
        }
      } else {
        // Deep-link phase handling (Phase 2.5)
        const requestedPhase = urlPhase as WizardPhase | undefined;
        if (requestedPhase && VALID_PHASES.includes(requestedPhase)) {
          const entityPhaseIdx = VALID_PHASES.indexOf(inferred);
          const requestedIdx = VALID_PHASES.indexOf(requestedPhase);
          if (requestedIdx <= entityPhaseIdx) {
            // URL step <= entity phase — honor URL (navigating back)
            setPhase(requestedPhase);
          } else {
            // URL step > entity phase — redirect to entity's phase (can't skip)
            setPhase(inferred);
          }
        } else {
          setPhase(inferred);
        }
      }
    }
  }, [loadedCast, urlPhase]);

  // Back navigation — go to previous USEFUL phase. We skip transient phases
  // (audio_generating) when going back from later phases because there's
  // nothing actionable for the user there — they'd just see a spinner that
  // re-finishes immediately. From editor or ready, back jumps to script.
  const PREV_PHASE: Record<WizardPhase, WizardPhase | null> = {
    setup: null,
    // Not reachable — the header (and its Back button) is hidden for the
    // whole generating_script phase, see the render below.
    generating_script: null,
    script: "setup",
    audio_generating: "script",
    editor: "script",
    ready: "editor",
  };
  const handleBack = useCallback(() => {
    const prev = PREV_PHASE[phase];
    if (!prev) return;
    setPhase(prev);
    if (cast?.id) navigate(`/cast-builder/${cast.id}/${prev}`, { replace: false });
  }, [phase, cast, navigate]);

  // Version restored handler — from VersionPicker
  const handleVersionRestored = useCallback((updatedCast: Cast) => {
    setCast(updatedCast);
    setPhase(statusToPhase(updatedCast.status));
  }, []);

  const handleCastCreated = useCallback(async (newCast: Cast, wasExisting?: boolean) => {
    setCast(newCast);
    // wasExisting = the user navigated back to Setup on an already-generated
    // cast and hit Continue again (e.g. just to tweak duration/quality) —
    // outline + script already exist and may have been reviewed/edited, so
    // don't regenerate and clobber them. Only fresh casts get a script run.
    if (!wasExisting) {
      setPhase("generating_script");
      try {
        await castsApi.generateScripts(newCast.id);
      } catch (err) {
        console.error("Auto script gen failed:", err);
      }
    }
    setPhase("script");
    navigate(`/cast-builder/${newCast.id}/script`, { replace: true });
  }, [navigate]);

  const handleScriptDone = useCallback((updatedCast: Cast) => {
    setCast(updatedCast);
    setPhase("audio_generating");
    // Seed React Query with the fresh cast so the next phase-deriving
    // useEffect (which reads `loadedCast`) doesn't see the STALE
    // SCRIPT_REVIEW status and yank the user back to the script phase.
    // Without this seed, the user has to click Generate Audio twice:
    // first click navigates forward but stale-status snaps back to script,
    // second click works because RQ has refetched naturally by then.
    queryClient.setQueryData(["cast", updatedCast.id], updatedCast);
    queryClient.invalidateQueries({ queryKey: ["cast", updatedCast.id] });
    if (updatedCast.id) navigate(`/cast-builder/${updatedCast.id}/audio_generating`, { replace: false });
  }, [navigate, queryClient]);

  const handleAudioReady = useCallback((updatedCast: Cast) => {
    setCast(updatedCast);
    setPhase("editor");
    // Same seed-then-invalidate pattern as handleScriptDone — prevents the
    // useEffect from snapping the user back to audio_generating because
    // React Query still holds an in-flight TTS_GENERATING status.
    queryClient.setQueryData(["cast", updatedCast.id], updatedCast);
    queryClient.invalidateQueries({ queryKey: ["cast", updatedCast.id] });
    if (updatedCast.id) navigate(`/cast-builder/${updatedCast.id}/editor`, { replace: false });
  }, [navigate, queryClient]);

  const handleFinalize = useCallback(() => {
    // Stay on editor — render progress is shown inline in the button
    setPhase("editor");
    if (cast?.id) navigate(`/cast-builder/${cast.id}/editor`, { replace: false });
  }, [cast, navigate]);

  const [rendering, setRendering] = useState(false);
  const [cancellingRender, setCancellingRender] = useState(false);
  // Track local edits — any change after a render invalidates "ready" status
  const editsSinceRenderRef = useRef(0);
  // Which render id we've already told the Billing page's usage query to
  // refetch for — the poll below re-evaluates "is the latest render ready"
  // every 5s, so without this we'd invalidate on every tick forever instead
  // of once per completed render.
  const billingInvalidatedForRenderRef = useRef<string | null>(null);

  // Track render status for inline button display
  const [renderStatus, setRenderStatus] = useState<{
    status: "idle" | "rendering" | "ready" | "failed";
    progress?: string;
    progressStep?: string;
    progressPercent?: number;
    queuePosition?: number;
    renderStatusRaw?: string;
    renderId?: string;
    errorMessage?: string;
    // New: per-block statuses + aggregate ETA so the header pill can show a
    // dropdown with per-block rows and an overall "~Xs" estimate.
    blocks?: import("@/components/cast-builder/RenderStatusPill").RenderBlockStatus[];
    etaSeconds?: number | null;
    bakingCompleted?: number;
    bakingTotal?: number;
    renderCreatedAt?: string | null;
  }>({ status: "idle" });

  // Poll render status continuously (not just in editor/ready phases) — Setup
  // and Script need a live view of "is a render active" too, so they can lock
  // editing while one is in flight (a mid-render edit can corrupt the output,
  // since the render task reads several fields live rather than from a
  // frozen snapshot).
  useEffect(() => {
    if (!cast?.id) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await castsApi.listRenders(cast.id);
        const rendersList = Array.isArray(data) ? data : [];
        const active = rendersList.find((r: any) => ["queued", "baking", "composing"].includes(r.status));
        const latest = rendersList[0];
        if (active) {
          const pct = active.progress_percent || 0;
          const step = active.progress_step || (
            active.status === "queued" ? `Queued #${active.queue_position || "..."}` :
              active.status === "composing" ? "Composing final video..." :
                `Baking ${active.baking_chunks_completed || 0}/${active.baking_chunks_total || "?"}...`
          );
          setRenderStatus({
            status: "rendering",
            progress: `${pct}%`,
            progressStep: step,
            progressPercent: pct,
            queuePosition: active.queue_position,
            renderStatusRaw: active.status,
            renderId: active.id,
            // New fields exposed by the enriched /renders endpoint. Older servers
            // simply won't return these — the pill falls back to the aggregate
            // bakingCompleted / bakingTotal counts.
            blocks: Array.isArray(active.blocks) ? active.blocks : [],
            etaSeconds: typeof active.eta_seconds === "number" ? active.eta_seconds : null,
            bakingCompleted: active.baking_chunks_completed || 0,
            bakingTotal: active.baking_chunks_total || 0,
            renderCreatedAt: active.created_at ?? null,
          });
        } else if (latest?.status === "ready") {
          // Content-hash based stale detection: compare render's timeline hash
          // against the current editor timeline hash
          let isStale = editsSinceRenderRef.current > 0;
          if (!isStale && latest.timeline_hash && arrangePhaseRef.current) {
            try {
              const { computeTimelineHash } = await import("@/lib/timelineHash");
              const currentTracks = arrangePhaseRef.current.getTimelineTracks?.() || [];
              if (currentTracks.length > 0) {
                const currentHash = await computeTimelineHash(currentTracks);
                isStale = currentHash !== latest.timeline_hash;
              }
            } catch { /* hash comparison failed, fall back to timestamp */ }
          }
          // Fallback: timestamp-based if no hash available. cast_renders has
          // no timeline_hash column today, so this fallback is actually the
          // only path that ever runs. The render-completion process itself
          // touches the casts row (status/video fields) a few ms AFTER
          // stamping completed_at, so a bare `castUpdated > renderTime`
          // treats that routine bookkeeping write as if the user had edited
          // the timeline — the button then never correctly flips to "Render
          // Ready" right after a successful render. A small buffer absorbs
          // that same-instant bookkeeping write while still catching a
          // genuine edit made afterward.
          if (!isStale && !latest.timeline_hash) {
            const renderTime = new Date(latest.completed_at || latest.created_at).getTime();
            const castUpdated = new Date((cast as any).updated_at || 0).getTime();
            const STALE_BUFFER_MS = 5000;
            isStale = castUpdated > renderTime + STALE_BUFFER_MS;
          }
          if (!isStale && billingInvalidatedForRenderRef.current !== latest.id) {
            billingInvalidatedForRenderRef.current = latest.id;
            // Render just completed and actually billed real usage — the
            // Billing page's Cost Breakdown query has its own staleTime and
            // nothing else tells it a new billable action happened, so a
            // quick nav back to Billing right after a render used to show
            // stale numbers. Force it to refetch next time it's mounted.
            queryClient.invalidateQueries({ queryKey: ["my-usage"] });
          }
          setRenderStatus({
            status: isStale ? "idle" : "ready",
            renderId: latest.id,
            ...(isStale ? { errorMessage: "Changes since last render" } : {}),
          });
        } else if (latest?.status === "failed" || latest?.status === "cancelled") {
          // Cancelled reuses the failed-state UI (message, Retry button) —
          // error_message already reads "Cancelled by user…" from the
          // /cancel endpoint, so no separate copy is needed here.
          setRenderStatus({ status: "failed", errorMessage: latest.error_message, renderId: latest.id });
        } else {
          setRenderStatus({ status: "idle" });
        }
      } catch { /* silent */ }
    };
    poll();
    const interval = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [cast?.id]);

  const handleCancelRender = useCallback(async () => {
    if (!cast?.id || !renderStatus.renderId || cancellingRender) return;
    setCancellingRender(true);
    try {
      const result = await castsApi.cancelRender(cast.id, renderStatus.renderId);
      setRenderStatus({ status: "failed", errorMessage: result.error_message, renderId: result.id });
      toast({ title: "Render cancelled", description: "Blocks already baking will finish, but no further work will be queued." });
    } catch (err: any) {
      toast({
        title: "Couldn't cancel render",
        description: err?.response?.data?.detail || "Please try again.",
        variant: "destructive",
      });
    } finally {
      setCancellingRender(false);
    }
  }, [cast?.id, renderStatus.renderId, cancellingRender, toast]);

  const handleFinalizeClick = useCallback(async () => {
    if (!cast) return;

    // Phase 3.0.3 — Stale audio warning: only warn if audio is truly stale AND not a retry
    if ((cast as any).audio_stale_since && renderStatus.status !== "failed") {
      try {
        const proceed = confirm(
          "Audio has been updated but not re-generated. Continue with current audio?"
        );
        if (!proceed) return;
      } catch { /* confirm blocked — proceed anyway */ }
    }

    const MIN_SPEAKING_SLOT_S = 1.5;
    try {
      const regions = arrangePhaseRef.current?.getBlockRegions?.() || [];
      const shortBlocks = regions
        .map((r) => {
          const block = (cast.blocks || []).find((b) => b.id === r.block_id);
          const ttsSeconds = block?.variants?.find((v) => v.tts_duration_seconds)?.tts_duration_seconds;
          const slotSeconds = r.end_s - r.start_s;
          return { block, slotSeconds, ttsSeconds };
        })
        .filter((x) => x.ttsSeconds && x.slotSeconds < MIN_SPEAKING_SLOT_S);

      if (shortBlocks.length > 0) {
        const names = shortBlocks
          .map((x) => `${x.slotSeconds.toFixed(2)}s clip (needs ~${x.ttsSeconds!.toFixed(1)}s)`)
          .join(", ");
        toast({
          title: shortBlocks.length === 1 ? "A clip is too short to render" : "Some clips are too short to render",
          description: `${names} — extend it on the timeline (drag its edge out) so it's long enough for the voiceover, then render again.`,
          variant: "destructive",
        });
        return;
      }
    } catch (preflightErr) {
      console.warn("Pre-flight duration check failed, proceeding anyway:", preflightErr);
    }

    setRendering(true);
    setRenderStatus({ status: "idle" });  // Reset to idle immediately on retry
    editsSinceRenderRef.current = 0;  // Reset edit counter when rendering starts
    try {
      // Flush pending editor save before dispatching render — prevents edit loss
      try { await arrangePhaseRef.current?.flushSave(); } catch (flushErr) { console.warn("Flush save failed:", flushErr); }
      const finalizeResult = await castsApi.finalize(cast.id);
      const bonded = finalizeResult?.layer_summary?.bonded_blocks ?? 0;
      const overlayStr = Object.entries(finalizeResult?.layer_summary?.overlays || {})
        .filter(([, v]) => (v as number) > 0)
        .map(([k, v]) => `${v} ${k}${(v as number) !== 1 ? "s" : ""}`)
        .join(", ");
      toast({ title: "Rendering started", description: `${bonded} avatar block${bonded !== 1 ? "s" : ""}${overlayStr ? ` \u00b7 ${overlayStr}` : ""}` });
      handleFinalize();
    } catch (err: any) {
      const detail = String(err?.response?.data?.detail || err?.message || "Failed to start rendering");
      // Phase 3.0.3 — Friendly messages for known error cases
      if (detail.includes("No timeline data") || detail.includes("arrange the timeline")) {
        toast({
          title: "Add content before finalizing",
          description: "Open the editor and arrange your timeline before rendering.",
          variant: "destructive",
        });
      } else if (detail.includes("No bonded blocks")) {
        toast({
          title: "Add content before finalizing",
          description: "Your timeline has no content blocks. Add audio and video elements first.",
          variant: "destructive",
        });
      } else {
        toast({
          title: "Render failed to start",
          description: detail,
          variant: "destructive",
        });
      }
    } finally {
      setRendering(false);
    }
  }, [cast, handleFinalize]);

  const handleRenderReady = useCallback((updatedCast: Cast) => {
    setCast(updatedCast);
    setPhase("ready");
    if (updatedCast.id) navigate(`/cast-builder/${updatedCast.id}/ready`, { replace: false });
  }, [navigate]);

  const handleEditFromReady = useCallback(() => {
    setPhase("editor");
    if (cast?.id) navigate(`/cast-builder/${cast.id}/editor`, { replace: false });
  }, [cast, navigate]);

  const handleEditScript = useCallback(() => {
    setPhase("script");
    if (cast?.id) navigate(`/cast-builder/${cast.id}/script`, { replace: false });
  }, [cast, navigate]);

  const handlePhaseClick = useCallback((targetPhase: WizardPhase) => {
    setPhase(targetPhase);
    if (cast?.id) navigate(`/cast-builder/${cast.id}/${targetPhase}`, { replace: false });
  }, [cast, navigate]);

  const handleError = useCallback((errMsg: string) => {
    setError(errMsg);
  }, []);

  const handleDeleteCast = useCallback(async () => {
    if (!cast) return;
    if (!confirm("Delete this cast? All clips will be lost.")) return;
    try {
      await castsApi.delete(cast.id);
      toast({ title: "Cast deleted" });
      navigate("/cast-builder");
    } catch (err: any) {
      toast({
        title: "Delete failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    }
  }, [cast, navigate]);

  const [kebabOpen, setKebabOpen] = useState(false);
  const kebabRef = useRef<HTMLDivElement>(null);

  // Click-outside close for kebab menu
  useEffect(() => {
    if (!kebabOpen) return;
    const handler = (e: MouseEvent) => {
      if (kebabRef.current && !kebabRef.current.contains(e.target as Node)) {
        setKebabOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [kebabOpen]);

  const handleDuplicateAs = useCallback(async () => {
    if (!cast) return;
    const currentFormat = (cast as any).format_family || "vertical";
    const targetFormat = currentFormat === "vertical" ? "horizontal" : "vertical";
    setKebabOpen(false);
    try {
      const result = await castsApi.duplicateAs(cast.id, targetFormat);
      toast({
        title: `Created ${targetFormat} version`,
        description: `"${cast.name || "Untitled"}" duplicated. Layout starts blank.`,
      });
      navigate(`/cast-builder/${result.cast_id}`);
    } catch (err: any) {
      toast({
        title: "Duplication failed",
        description: err?.response?.data?.detail || err.message || "Failed to duplicate cast",
        variant: "destructive",
      });
    }
  }, [cast, navigate]);

  // Show delete button in all phases except LIVE and GENERATING
  const canDelete = cast && !["live", "generating"].includes(cast.status?.toLowerCase() || "");

  const handleRetry = useCallback(async () => {
    if (!cast) return;
    setError(null);
    try {
      await castsApi.retry(cast.id);
      toast({ title: "Retrying generation..." });
      const refreshed = await castsApi.get(cast.id);
      setCast(refreshed);
      setPhase(statusToPhase(refreshed.status));
    } catch (err: any) {
      toast({
        title: "Retry failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    }
  }, [cast]);

  if (!isNew && isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="w-6 h-6 animate-spin text-accent" />
      </div>
    );
  }

  const currentCastId = cast?.id || castId;

  return (
    <div className="flex flex-col h-full">
      {/* Phase header */}
      {!(isNew && phase === "setup") && phase !== "generating_script" && (
        <div className="flex items-center">
          {/* Back button — shown on all phases except setup */}
          {phase !== "setup" && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleBack}
              className="ml-2 text-white/50 hover:text-white px-2"
              data-testid="cast-back-btn"
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
          )}
          <div className="flex-1">
            <PhaseHeader
              currentPhase={phase}
              castName={cast?.name}
              onPhaseClick={handlePhaseClick}
              renderState={
                // FIX 9 — Ready step coloring. The polling effect maps
                // server state into renderStatus already: "ready" means the
                // latest render matches the current timeline, "idle" with
                // errorMessage="Changes since last render" means a render
                // exists but it's stale due to edits.
                renderStatus.status === "ready"
                  ? "ready"
                  : renderStatus.errorMessage === "Changes since last render" && renderStatus.renderId
                    ? "stale"
                    : "none"
              }
              actions={phase === "editor" && cast ? (
                <div className="flex items-center gap-2">
                  {/* Left: Version picker */}
                  <VersionPicker
                    cast={cast}
                    onRestored={handleVersionRestored}
                    onForked={(newVersion) => {
                      editsSinceRenderRef.current += 1;
                      setRenderStatus((prev) => prev.status === "ready" ? { status: "idle" } : prev);
                      // Directly update cast version in state — bypasses React Query cache entirely
                      setCast((prev) => prev ? { ...prev, version: newVersion } : prev);
                    }}
                    changeCount={arrangePhaseRef.current?.getChangeCount?.() ?? 0}
                  />

                  {/* Render status: new pill with per-block dropdown.
                      Scales to 100+ blocks; shows Host/Mod/Pod per block. */}
                  {renderStatus.status === "rendering" ? (
                    <RenderStatusPill
                      renderStatusRaw={renderStatus.renderStatusRaw}
                      progressPercent={renderStatus.progressPercent}
                      progressStep={renderStatus.progressStep}
                      queuePosition={renderStatus.queuePosition}
                      etaSeconds={renderStatus.etaSeconds}
                      blocks={renderStatus.blocks}
                      bakingCompleted={renderStatus.bakingCompleted}
                      bakingTotal={renderStatus.bakingTotal}
                      renderCreatedAt={renderStatus.renderCreatedAt}
                      onCancel={handleCancelRender}
                      cancelling={cancellingRender}
                    />
                  ) : renderStatus.status === "ready" ? (
                    <Button
                      onClick={() => {
                        rendersRef.current?.playLatestRender();
                      }}
                      className="bg-green-500/20 text-green-300 border border-green-500/30 hover:bg-green-500/30"
                      data-testid="finalize-render-btn"
                    >
                      <CheckCircle2 className="w-4 h-4 mr-2" />
                      Render Ready
                    </Button>
                  ) : renderStatus.status === "failed" ? (
                    <Button
                      onClick={handleFinalizeClick}
                      className="bg-red-500/20 text-red-300 border border-red-500/30 hover:bg-red-500/30"
                      data-testid="finalize-render-btn"
                    >
                      <XCircle className="w-4 h-4 mr-2" />
                      Render Failed — Retry
                    </Button>
                  ) : (
                    <Button
                      onClick={handleFinalizeClick}
                      disabled={rendering}
                      className="bg-accent hover:bg-accent-hover text-white"
                      data-testid="finalize-render-btn"
                    >
                      {rendering ? (
                        <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Starting render...</>
                      ) : (
                        <><Rocket className="w-4 h-4 mr-2" /> Finalize & Render</>
                      )}
                    </Button>
                  )}

                  {/* Right: Renders collection */}
                  <RendersCollection ref={rendersRef} castId={cast.id} />

                  {/* FIX 1.2 — Kebab menu with Duplicate + Delete cast */}
                  <div className="relative" ref={kebabRef}>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setKebabOpen(v => !v)}
                      className="text-white/60 hover:text-white p-1"
                      data-testid="cast-kebab-menu"
                    >
                      <MoreVertical className="w-4 h-4" />
                    </Button>
                    {kebabOpen && (
                      <div className="absolute right-0 top-full mt-1 bg-[#1a1a2e] border border-white/10 rounded-lg shadow-xl min-w-[200px] z-50 py-1">
                        <button
                          onClick={handleDuplicateAs}
                          className="w-full text-left px-4 py-2 text-sm text-white/80 hover:bg-white/10 flex items-center gap-2"
                          data-testid="duplicate-as-btn"
                        >
                          <Copy className="w-4 h-4" />
                          Duplicate as {((cast as any).format_family || "vertical") === "vertical" ? "horizontal" : "vertical"}
                        </button>
                        {canDelete && (
                          <button
                            onClick={() => { setKebabOpen(false); handleDeleteCast(); }}
                            className="w-full text-left px-4 py-2 text-sm text-red-400 hover:bg-white/10 flex items-center gap-2"
                            data-testid="delete-cast-kebab-btn"
                          >
                            <Trash2 className="w-4 h-4" />
                            Delete cast
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ) : undefined}
            />
          </div>
        </div>
      )}

      {/* Back button for new cast setup */}
      {isNew && phase === "setup" && (
        <div className="px-6 pt-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/cast-builder")}
            className="text-white/50 hover:text-white"
          >
            <ArrowLeft className="w-4 h-4 mr-1" /> Back to My Casts
          </Button>
        </div>
      )}

      {/* Error overlay */}
      {error && (
        <div className="mx-6 mt-4 rounded-lg bg-red-500/10 border border-red-500/30 p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-400 shrink-0" />
            <div>
              <p className="text-sm font-medium text-red-300">Generation Failed</p>
              <p className="text-xs text-red-400/70 mt-0.5">{error}</p>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={handleRetry} className="border-red-500/30 text-red-300">
            <RefreshCw className="w-3 h-3 mr-1" /> Retry
          </Button>
        </div>
      )}

      {/* Phase content */}
      <div className={phase === "editor" ? "flex-1 overflow-hidden min-h-0" : "flex-1 overflow-auto"}>
        {phase === "setup" && (
          <SetupPhase
            cast={cast}
            onCreated={handleCastCreated}
            renderInProgress={renderStatus.status === "rendering"}
            onCancelRender={handleCancelRender}
          />
        )}

        {phase === "generating_script" && <ScriptGeneratingPhase />}

        {phase === "script" && cast && (
          <ScriptPhase
            cast={cast}
            onDone={handleScriptDone}
            renderInProgress={renderStatus.status === "rendering"}
            onCancelRender={handleCancelRender}
          />
        )}

        {phase === "audio_generating" && currentCastId && (
          <AudioGeneratingPhase
            castId={currentCastId}
            onReady={handleAudioReady}
            onError={handleError}
          />
        )}

        {phase === "editor" && cast && (
          <ArrangePhase ref={arrangePhaseRef} cast={cast} onEditScript={handleEditScript} onEdited={() => {
            editsSinceRenderRef.current += 1;
            // Instant UI flip: if showing "Render Ready" and user edits, switch to idle immediately
            setRenderStatus((prev) => {
              if (prev.status === "ready") {
                return { status: "idle", errorMessage: "Changes since last render" };
              }
              return prev;
            });
          }} />
        )}

        {phase === "ready" && cast && (
          <ReadyPhase cast={cast} onEdit={handleEditFromReady} onEditScript={handleEditScript} />
        )}
      </div>
    </div>
  );
}