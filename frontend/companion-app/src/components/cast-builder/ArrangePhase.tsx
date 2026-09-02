/**
 * ArrangePhase — Remotion Editor Starter timeline editor for the Cast Builder.
 *
 * Mounts <LuminacastEditor> with the cast's timeline data and an auto-save bridge
 * that converts Editor Starter state → renderer-compatible bonded V1/A1 snapshot
 * via editorStarterToLuminacastSnapshot(), then POSTs to castsApi.saveTimeline().
 *
 * The `twick_data` field name is kept for backward compat. Its content is now
 * the OUTPUT of editorStarterToLuminacastSnapshot() — the renderer-format payload,
 * NOT Editor Starter's native state. The backend's extract_bonded_blocks_from_timeline()
 * is the source of truth for the shape.
 */
import { useEffect, useState, useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { castsApi, avatarApi } from "@/lib/api";
import {
  castToEditorStarterTimeline,
  editorStarterToLuminacastSnapshot,
  computeBlockRegions,
  getCanvasSize,
} from "@/lib/editorStarterMapping";
import type { UndoableState } from "@/components/cast-builder/editor-starter/state/types";
import { LuminacastEditor } from "@/components/cast-builder/editor-starter";
import { RenderLockBanner } from "@/components/cast-builder/RenderLockBanner";
import type { Cast } from "@/lib/types";
import { Loader2, ExternalLink, RefreshCw } from "lucide-react";
import { toast } from "@/hooks/useToast";


interface ArrangePhaseProps {
  cast: Cast;
  onEditScript?: () => void;
  onEdited?: () => void;
  // True while a render is actively queued/baking/composing for this cast.
  // Makes the timeline editor read-only so a mid-render edit can't race the
  // render task's live reads — same treatment as the Setup / Script tabs.
  renderInProgress?: boolean;
  onCancelRender?: () => void;
}

export interface ArrangePhaseHandle {
  flushSave: () => Promise<void>;
  getChangeCount: () => number;
  getTimelineTracks: () => unknown[];
  getBlockRegions: () => import("@/lib/editorStarterMapping").BlockRegion[];

}


/** Auto-save debounce interval (ms) — matches the old Twick auto-save */
const AUTO_SAVE_DEBOUNCE_MS = 1500;

/** Audible fallback bed level when the cast has no explicit music_volume.
 *  Matches the historical MUSIC_DEFAULT_VOLUME and editorStarterMapping. */
const AUDIBLE_MUSIC_BED_DEFAULT = 0.15;

export const ArrangePhase = forwardRef<ArrangePhaseHandle, ArrangePhaseProps>(function ArrangePhase({ cast, onEditScript, onEdited, renderInProgress, onCancelRender }, ref) {
  const navigate = useNavigate();
  const [initialState, setInitialState] = useState<UndoableState | null>(null);
  const [loading, setLoading] = useState(true);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const changeCountRef = useRef(0);
  const latestStateRef = useRef<UndoableState | null>(null);

  // ── Background-music volume (Arrange-tab slider) ──────────────────────
  // `musicVolume` is passed live into <LuminacastEditor>; the editor's
  // ContextProvider reconciles the timeline's music item(s) to it on every
  // change WITHOUT remounting, so the bed volume updates while the preview
  // keeps playing. The PATCH just persists it (debounced) for the renderer
  // and for the next fresh load.
  const hasMusic = !!cast.background_music_url && (cast.music_track_choice ?? "auto") !== "off";
  const [musicVolume, setMusicVolume] = useState<number>(
    typeof cast.music_volume === "number" ? cast.music_volume : AUDIBLE_MUSIC_BED_DEFAULT,
  );
  const musicVolTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const handleMusicVolumeChange = useCallback((next: number) => {
    setMusicVolume(next); // live — flows into the editor immediately
    if (musicVolTimerRef.current) clearTimeout(musicVolTimerRef.current);
    musicVolTimerRef.current = setTimeout(() => {
      castsApi.patch(cast.id, { music_volume: next })
        .then(() => onEdited?.())
        .catch((e) => {
          console.error("music_volume patch failed:", e);
          toast({ title: "Couldn't save music volume", variant: "destructive" });
        });
    }, 500);
  }, [cast.id, onEdited]);

  /** Build the save payload from current editor state */
  const buildSavePayload = useCallback((state: UndoableState) => {
    const snapshot = editorStarterToLuminacastSnapshot(state);
    const blockRegions = computeBlockRegions(state);
    // Serialize the native editor state for later restore
    const editorState = {
      tracks: state.tracks,
      items: state.items,
      assets: state.assets,
      fps: state.fps,
      compositionWidth: state.compositionWidth,
      compositionHeight: state.compositionHeight,
      deletedAssets: state.deletedAssets,
      // Which active blocks existed at the moment of this save. On the next
      // load, an active block missing from the saved items is only treated
      // as "genuinely new, must rebuild" when it's ALSO absent here —
      // otherwise it's a block the user deliberately cut/removed from the
      // timeline, and re-deriving fresh from cast.blocks would silently
      // resurrect it (confirmed: cutting a block's clips, then refreshing,
      // brought the block right back — the restore guard below couldn't
      // tell "never saved yet" apart from "intentionally emptied out").
      known_block_ids: (cast.blocks || [])
        .filter((b: any) => b.is_active !== false && b.deleted_at == null)
        .map((b: any) => b.id),
    };
    return {
      variant_id: "default",
      twick_data: snapshot,
      block_regions: blockRegions,
      editor_state: editorState,
    };
  }, [cast.blocks]);

  // Expose flushSave to parent via ref — clears pending debounce and saves immediately
  useImperativeHandle(ref, () => ({
    async flushSave() {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = undefined;
      }
      const state = latestStateRef.current;
      // Always save when there's editor state. Previously this gated on
      // changeCountRef.current > 1, but that meant Finalize-on-fresh-open
      // (user enters editor and immediately clicks Render) wrote NO
      // timeline_json — finalize then 400'd with "No timeline data".
      // Saving the initial fresh state is cheap and idempotent.
      if (state) {
        await castsApi.saveTimeline(cast.id, buildSavePayload(state));
      }
    },
    getChangeCount() {
      return changeCountRef.current;
    },
    getTimelineTracks() {
      const state = latestStateRef.current;
      if (!state) return [];
      const snapshot = editorStarterToLuminacastSnapshot(state);
      return snapshot.tracks || [];
    },
    getBlockRegions() {
      const state = latestStateRef.current;
      if (!state) return [];
      return computeBlockRegions(state);
    },
  }), [cast.id, buildSavePayload]);

  // Auto-save on navigate away / tab close
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (latestStateRef.current && cast?.id) {
        const payload = buildSavePayload(latestStateRef.current);
        // navigator.sendBeacon() can only send POST, and this endpoint is
        // PUT-only — every beacon save was silently failing with 405, and
        // sendBeacon gives no way to observe or catch that. fetch's
        // `keepalive: true` gives the same "survives page unload" guarantee
        // while letting us use PUT against the actual working endpoint.
        let authHeader: string | undefined;
        try {
          const raw = localStorage.getItem("luminacast-auth");
          const token = raw ? JSON.parse(raw)?.state?.token : undefined;
          if (token) authHeader = "Bearer " + token;
        } catch { /* no token available — request will 401, nothing more we can do here */ }
        fetch("/api/casts/" + cast.id + "/timeline", {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            ...(authHeader ? { Authorization: authHeader } : {}),
          },
          body: JSON.stringify(payload),
          keepalive: true,
        }).catch(() => { /* best-effort save on exit */ });
      }
    };

    
    window.addEventListener("beforeunload", handleBeforeUnload);
    const handleVisChange = () => {
      if (document.visibilityState === "hidden") handleBeforeUnload();
    };
    document.addEventListener("visibilitychange", handleVisChange);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      document.removeEventListener("visibilitychange", handleVisChange);
      // Flush on unmount (navigating away within SPA)
      handleBeforeUnload();
    };
  }, [cast?.id, buildSavePayload]);

  // Phase 2.5.2 — Stale audio detection
  const [staleDismissed, setStaleDismissed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // Fetch siblings for the sibling banner
  const { data: siblingsData } = useQuery({
    queryKey: ["cast-siblings", cast.id],
    queryFn: () => castsApi.getSiblings(cast.id),
    enabled: !!cast.id,
    staleTime: 30_000,
  });

  const siblings = siblingsData?.siblings ?? [];
  const audioStaleSince = cast.audio_stale_since;

  // Load saved editor state or build fresh timeline from cast blocks
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Always re-fetch cast so newly added blocks are included
        const freshCast = await castsApi.get(cast.id);

        // Try to restore previously saved editor state. We only restore if the
        // saved state has an item for EVERY current active block. The previous
        // check was a count comparison (savedItemCount >= currentBlockCount)
        // which incorrectly accepts stale state when the user adds a new block
        // (e.g. a PIP block) after saving — the new block is missing from the
        // saved items but the count test passes because old blocks have
        // multiple items. Result: editor showed an outdated timeline missing
        // the new block.
        let restoredState: UndoableState | null = null;
        try {
          const savedTimeline = await castsApi.getTimeline(cast.id, "default");
          if (savedTimeline?.editor_state && savedTimeline.editor_state.tracks) {
            const items: Record<string, any> = savedTimeline.editor_state.items || {};
            // Collect block_ids that have at least one item bound to them, and
            // the longest bonded-audio duration (seconds) saved for each.
            const blockIdsInSaved = new Set<string>();
            const savedAudioDurationS = new Map<string, number>();
            const fps = savedTimeline.editor_state.fps || 30;
            for (const item of Object.values(items)) {
              const bid = item?.metadata?.block_id;
              if (!bid) continue;
              blockIdsInSaved.add(bid);
              if (item?.type === "audio" && item?.metadata?.bonded) {
                const durS = (item.durationInFrames || 0) / fps;
                savedAudioDurationS.set(bid, Math.max(savedAudioDurationS.get(bid) || 0, durS));
              }
            }
            const currentBlocks = (freshCast.blocks || [])
              .filter((b: any) => b.is_active !== false && b.deleted_at == null);
            // A block missing from blockIdsInSaved is ambiguous on its own:
            // it could be genuinely new (added since the last save — the
            // saved state is stale and must be rebuilt) or one the user
            // deliberately cut every item out of (the saved state is
            // CORRECT and must be kept as-is, or the cut gets silently
            // undone on every reload). known_block_ids (present on saves
            // made after this fix) disambiguates: only a block absent from
            // BOTH counts as "new enough to force a rebuild". Older saves
            // predate known_block_ids and fall back to the previous
            // (more conservative, cut-unaware) behavior.
            const knownBlockIds: string[] | undefined = savedTimeline.editor_state.known_block_ids;
            const allCurrentInSaved = knownBlockIds
              ? currentBlocks.every((b: any) => knownBlockIds.includes(b.id))
              : currentBlocks.every((b: any) => blockIdsInSaved.has(b.id));

            // Guard against a stale saved slot that's frozen far below the
            // block's live TTS duration — e.g. a snapshot saved before a
            // block's audio finished generating, or before it was later
            // regenerated through a path that doesn't patch the saved
            // timeline (bulk script/audio regen, sibling cascade before
            // "Refresh timeline" is clicked). Without this check, the stale
            // slot from ANY block_id present in the saved JSON gets reused
            // forever — the block_id-presence check above never verifies
            // durations, so the same broken slot re-saves on every autosave.
            //
            // An absolute floor alone isn't enough: render rnd_25a6a0c2b241
            // hard-failed on a block whose saved slot was 1.267s against a
            // refreshed tts_duration of 4.65s (27%) — comfortably above a
            // flat 1.0s floor, but still a stale leftover that head-trimmed
            // the baked clip down to an 89%-frozen slice the Phase 3
            // validator rejected outright. Mirrors the same ratio guard
            // added backend-side in tasks/cast_render.py.
            const STALE_DURATION_FLOOR_S = 1.0;
            const STALE_DURATION_RATIO = 0.35;
            // Separate from the ratio/floor check above (which catches GROSS
            // mismatches, e.g. a snapshot saved before audio finished
            // generating): this catches small precision shortfalls where the
            // saved slot is a hair under the live TTS duration — close enough
            // to pass the 35% tolerance, but still too short for Phase 3's
            // render validator, which requires slot >= tts_duration exactly.
            // A small epsilon avoids flagging floating-point noise as stale.
            const MARGINAL_SHORTFALL_EPSILON_S = 0.01;
            const staleBlocks: string[] = [];
            for (const b of currentBlocks) {
              const savedDurS = savedAudioDurationS.get(b.id);
              const liveVariant = (b.variants || []).find((v: any) => v.is_active !== false) || b.variants?.[0];
              const liveDurS = liveVariant?.tts_duration_seconds || liveVariant?.duration_seconds || 0;
              if (savedDurS == null) {
                // The saved timeline has NO audio item for this block. If the
                // block has since gained a script + baked TTS (common on
                // avatar_action blocks scripted after the timeline was last
                // saved), the snapshot is stale — rebuild so the voice track
                // is added. Silent beats (no script / no TTS) fall through
                // unchanged.
                const liveHasScript = !!((liveVariant?.script_text as string) || "").trim();
                if (liveHasScript && liveDurS > 0) staleBlocks.push(b.id);
                continue;
              }
              const staleThresholdS = Math.max(STALE_DURATION_FLOOR_S, liveDurS * STALE_DURATION_RATIO);
              const isGrosslyStale = liveDurS > 0 && savedDurS < staleThresholdS;
              const isMarginallyShort = liveDurS > 0 && savedDurS < liveDurS - MARGINAL_SHORTFALL_EPSILON_S;
              if (isGrosslyStale || isMarginallyShort) {
                staleBlocks.push(b.id);
              }
            }

            // A stale-output_format/orientation check was added here once
            // before (and reverted) to fix "layout change not reflected in
            // Arrange preview" — it forced a fresh rebuild whenever saved
            // compositionWidth/Height didn't match the cast's current
            // output_format. That fixed the orientation bug, but correlated
            // with reports of a blank preview canvas for UNCHANGED layouts
            // shortly after. The prior version compared against whatever
            // `output_format` was already in scope on the component, which
            // could still be the stale pre-refetch value the moment this
            // effect runs — producing false positives that routed casts with
            // no real orientation change through the same fresh-build path.
            // This version compares against `freshCast.output_format` (the
            // value just re-fetched above, same source of truth used to
            // build currentBlocks) and requires an exact pixel mismatch, so
            // an unrelated cast should never be misflagged as stale here.
            const savedCanvasW = savedTimeline.editor_state.compositionWidth;
            const savedCanvasH = savedTimeline.editor_state.compositionHeight;
            const expectedCanvas = getCanvasSize(freshCast.output_format);
            const orientationStale =
              savedCanvasW != null &&
              savedCanvasH != null &&
              (savedCanvasW !== expectedCanvas.width || savedCanvasH !== expectedCanvas.height);

            if (allCurrentInSaved && currentBlocks.length > 0 && staleBlocks.length === 0 && !orientationStale) {
              restoredState = savedTimeline.editor_state as UndoableState;
              console.log("RESTORED saved editor state:", {
                savedItemCount: Object.keys(items).length,
                currentBlockCount: currentBlocks.length,
                savedAt: savedTimeline.saved_at,
              });
            } else if (orientationStale) {
              console.log("Saved editor state canvas size doesn't match current output_format — rebuilding fresh.", {
                savedCanvasW, savedCanvasH, expectedCanvas, outputFormat: freshCast.output_format,
              });
            } else if (staleBlocks.length > 0) {
              console.log("Saved editor state has implausibly short slots — rebuilding fresh from live TTS durations. Affected block_ids:", staleBlocks);
            } else {
              const missing = knownBlockIds
                ? currentBlocks.filter((b: any) => !knownBlockIds.includes(b.id)).map((b: any) => b.id)
                : currentBlocks.filter((b: any) => !blockIdsInSaved.has(b.id)).map((b: any) => b.id);
              console.log("Saved editor state stale — rebuilding. New block_ids not seen at last save:", missing);
            }
          }
        } catch (err) {
          console.warn("Could not load saved timeline, building fresh:", err);
        }

        if (restoredState && !cancelled) {
          // The editor's ContextProvider reconciles music-item volume to the
          // live `musicVolume` prop on mount, so a stale saved timeline (no
          // metadata.volume, or a different level) is healed there — no need
          // to re-stamp it here.
          setInitialState(restoredState);
          setLoading(false);
          return;
        }

        // No saved state or outdated — build fresh from blocks
        let avatarFaceKey: string | undefined;
        let avatarName: string | undefined;
        const avatarId = freshCast.avatar_id || cast.avatar_id;
        if (avatarId) {
          try {
            const avatar = await avatarApi.status(avatarId);
            avatarFaceKey = avatar?.face_ref_key ?? undefined;
            avatarName = avatar?.name ?? undefined;
          } catch (err) {
            console.warn("Failed to fetch avatar face_ref_key:", err);
          }
        }

        console.log("BRIDGE INPUT (fresh build):", { avatarFaceKey, avatarName, castId: freshCast.id, blockCount: freshCast.blocks?.length });
        const { state } = castToEditorStarterTimeline(freshCast, {
          avatarFaceKey,
          avatarName,
          fps: 30,
        });

        if (!cancelled) {
          setInitialState(state);
          setLoading(false);
        }
      } catch (err) {
        console.error("Failed to build initial timeline:", err);
        // Fallback: build from prop cast data
        let avatarFaceKey: string | undefined;
        let avatarName: string | undefined;
        if (cast.avatar_id) {
          try {
            const avatar = await avatarApi.status(cast.avatar_id);
            avatarFaceKey = avatar?.face_ref_key ?? undefined;
            avatarName = avatar?.name ?? undefined;
          } catch { /* ignore */ }
        }
        const { state } = castToEditorStarterTimeline(cast, { avatarFaceKey, avatarName, fps: 30 });
        if (!cancelled) {
          setInitialState(state);
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [cast.id, cast.avatar_id]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      if (musicVolTimerRef.current) clearTimeout(musicVolTimerRef.current);
    };
  }, []);

  // Phase 2.5.3 — Poll for sibling cascade updates while user is editing.
  // If audio becomes stale mid-session, show a non-blocking toast instead
  // of disrupting the user's editing session.
  useEffect(() => {
    if (!cast.id || !siblings.length) return;
    const interval = setInterval(async () => {
      try {
        const refreshed = await castsApi.get(cast.id);
        const stale = refreshed.audio_stale_since;
        if (stale && !staleDismissed) {
          toast({
            title: "Audio updated by sibling",
            description: `The ${siblings[0]?.format_family || "other"} version updated audio. Click Refresh in the banner to apply.`,
          });
          // Clear interval after first notification to avoid spam
          clearInterval(interval);
        }
      } catch { /* silent — polling failure is non-critical */ }
    }, 30_000); // poll every 30s
    return () => clearInterval(interval);
  }, [cast.id, siblings.length, staleDismissed]);

  /**
   * Auto-save bridge: debounced callback fired by ContextProvider when
   * undoableState changes. Converts to renderer format and POSTs.
   */
  const handleStateChange = useCallback((undoableState: UndoableState) => {
    // Track latest state for flushSave
    latestStateRef.current = undoableState;

    // Skip the initial state load (changeCount=0 means first render)
    changeCountRef.current += 1;
    if (changeCountRef.current <= 1) return;
    onEdited?.();  // Notify parent that edits were made (invalidates render status)

    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        await castsApi.saveTimeline(cast.id, buildSavePayload(undoableState));
        console.log("AUTO-SAVE OK", new Date().toISOString());
      } catch (e) {
        console.error("Auto-save failed:", e);
      }
    }, AUTO_SAVE_DEBOUNCE_MS);
  }, [cast.id, buildSavePayload]);

  // Phase 2.5.2 — Refresh timeline from sibling cascade
  const handleRefreshTimeline = useCallback(async () => {
    setRefreshing(true);
    try {
      // Re-fetch the cast to get updated variants from sibling cascade
      const refreshed = await castsApi.get(cast.id);
      // Rebuild the editor state with updated cast data
      let avatarFaceKey: string | undefined;
      let avatarName: string | undefined;
      if (cast.avatar_id) {
        try {
          const avatar = await avatarApi.status(cast.avatar_id);
          avatarFaceKey = avatar?.face_ref_key ?? undefined;
          avatarName = avatar?.name ?? undefined;
        } catch { /* ignore */ }
      }
      console.log("BRIDGE INPUT:", { avatarFaceKey, avatarName, castId: refreshed.id, blockCount: refreshed.blocks?.length });
      const { state } = castToEditorStarterTimeline(refreshed, {
        avatarFaceKey,
        avatarName,
        fps: 30,
      });
      setInitialState(state);
      changeCountRef.current = 0; // Reset change counter to avoid immediate auto-save
      setStaleDismissed(true);
      // Clear stale flag on backend
      await castsApi.patch(cast.id, { clear_audio_stale: true } as any);
      toast({ title: "Timeline refreshed", description: "Updated audio from sibling cast applied." });
    } catch (err: any) {
      toast({
        title: "Refresh failed",
        description: err?.response?.data?.detail || err.message || "Could not refresh timeline",
        variant: "destructive",
      });
    } finally {
      setRefreshing(false);
    }
  }, [cast.id, cast.avatar_id]);

  if (loading || !initialState) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-[#0a0a0a]">
        <div className="flex items-center gap-2 text-white/50">
          <Loader2 className="w-5 h-5 animate-spin" />
          Loading editor...
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {renderInProgress && <RenderLockBanner onCancelRender={onCancelRender} />}
      {/* Everything below is made read-only while a render is running so a
          mid-render timeline edit can't race the render task (matches the
          Setup / Script tabs). */}
      <div
        className={
          "flex-1 min-h-0 flex flex-col" + (renderInProgress ? " opacity-60" : "")
        }
        inert={renderInProgress}
      >
      {/* Phase 2.5.2 — Stale audio notification banner */}
      {audioStaleSince && !staleDismissed && (
        <div className="flex items-center gap-3 bg-amber-500/10 border-b border-amber-500/20 px-4 py-2 text-sm text-amber-200 shrink-0">
          <span>
            Audio was updated in{" "}
            {siblings.length > 0 ? (
              <>the <strong>{siblings[0].format_family}</strong> version</>
            ) : (
              "a sibling cast"
            )}
            .
          </span>
          <button
            onClick={handleRefreshTimeline}
            disabled={refreshing}
            className="inline-flex items-center gap-1 text-amber-300 hover:text-amber-100 underline underline-offset-2 disabled:opacity-50"
          >
            {refreshing ? (
              <><Loader2 className="w-3 h-3 animate-spin" /> Refreshing...</>
            ) : (
              <><RefreshCw className="w-3 h-3" /> Refresh timeline</>
            )}
          </button>
          <button
            onClick={() => setStaleDismissed(true)}
            className="ml-auto text-amber-400/60 hover:text-amber-300 text-xs"
          >
            Dismiss
          </button>
        </div>
      )}
      {/* Sibling banner — Phase 2.4.5 */}
      {siblings.length > 0 && (
        <div className="flex items-center gap-3 bg-purple-500/10 border-b border-purple-500/20 px-4 py-2 text-sm text-purple-200 shrink-0">
          <span>
            This cast has a{" "}
            <strong>{siblings[0].format_family}</strong> version:{" "}
            <em>{siblings[0].name || "Untitled"}</em>
          </span>
          <button
            onClick={() => navigate(`/cast-builder/${siblings[0].cast_id}`)}
            className="inline-flex items-center gap-1 text-purple-300 hover:text-purple-100 underline underline-offset-2"
          >
            Open <ExternalLink className="w-3 h-3" />
          </button>
          {siblings.length > 1 && (
            <span className="text-purple-400/60 text-xs">
              +{siblings.length - 1} more
            </span>
          )}
        </div>
      )}
      {/* Background-music volume — the auto/generated bed plays under the
          narration in both the preview and the final render. Dragging updates
          the preview live (the editor reconciles its music item to this
          value without remounting); the value is persisted, debounced, to
          cast.music_volume for the renderer. */}
      {hasMusic && (
        <div className="flex items-center gap-3 bg-white/[0.03] border-b border-white/10 px-4 py-2 text-xs text-white/60 shrink-0">
          <span className="shrink-0 font-medium text-white/70">Music volume</span>
          <input
            type="range"
            min={0}
            max={0.6}
            step={0.01}
            value={musicVolume}
            onChange={(e) => handleMusicVolumeChange(Number(e.target.value))}
            className="flex-1 max-w-xs accent-accent"
            aria-label="Background music volume"
          />
          <span className="shrink-0 tabular-nums w-10 text-right text-white/50">
            {Math.round((musicVolume / 0.6) * 100)}%
          </span>
          <span className="shrink-0 text-white/30">Applies to preview &amp; render</span>
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-hidden">
        <LuminacastEditor
          cast={cast}
          initialUndoableState={initialState}
          onUndoableStateChange={handleStateChange}
          musicVolume={hasMusic ? musicVolume : undefined}
        />
      </div>
      </div>
    </div>
  );
});
