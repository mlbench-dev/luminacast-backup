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
import type { Cast } from "@/lib/types";
import { Loader2, ExternalLink, RefreshCw } from "lucide-react";
import { toast } from "@/hooks/useToast";

interface ArrangePhaseProps {
  cast: Cast;
  onEditScript?: () => void;
  onEdited?: () => void;
}

export interface ArrangePhaseHandle {
  flushSave: () => Promise<void>;
  getChangeCount: () => number;
  getTimelineTracks: () => unknown[];
  getBlockRegions: () => import("@/lib/editorStarterMapping").BlockRegion[];

}

/** Auto-save debounce interval (ms) — matches the old Twick auto-save */
const AUTO_SAVE_DEBOUNCE_MS = 1500;

export const ArrangePhase = forwardRef<ArrangePhaseHandle, ArrangePhaseProps>(function ArrangePhase({ cast, onEditScript, onEdited }, ref) {
  const navigate = useNavigate();
  const [initialState, setInitialState] = useState<UndoableState | null>(null);
  const [loading, setLoading] = useState(true);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const changeCountRef = useRef(0);
  const latestStateRef = useRef<UndoableState | null>(null);

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
    };
    return {
      variant_id: "default",
      twick_data: snapshot,
      block_regions: blockRegions,
      editor_state: editorState,
    };
  }, []);

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
        navigator.sendBeacon(
          "/api/casts/" + cast.id + "/timeline",
          JSON.stringify(payload)
        );
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
            const allCurrentInSaved = currentBlocks.every((b: any) => blockIdsInSaved.has(b.id));

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
              if (savedDurS == null) continue; // no bonded audio item — handled elsewhere
              const liveVariant = (b.variants || []).find((v: any) => v.is_active !== false) || b.variants?.[0];
              const liveDurS = liveVariant?.tts_duration_seconds || liveVariant?.duration_seconds || 0;
              const staleThresholdS = Math.max(STALE_DURATION_FLOOR_S, liveDurS * STALE_DURATION_RATIO);
              const isGrosslyStale = liveDurS > 0 && savedDurS < staleThresholdS;
              const isMarginallyShort = liveDurS > 0 && savedDurS < liveDurS - MARGINAL_SHORTFALL_EPSILON_S;
              if (isGrosslyStale || isMarginallyShort) {
                staleBlocks.push(b.id);
              }
            }

            // Guard against a saved snapshot from a different output_format —
            // e.g. saved as 9:16, then the user goes back to Setup and picks
            // 16:9. Block presence and audio durations can both still check
            // out, but the saved canvas dimensions no longer match the
            // cast's current layout, so the preview would silently keep
            // rendering the old orientation with no error or stale-cache
            // signal visible to the user.
            const expectedSize = getCanvasSize(freshCast.output_format);
            const savedWidth = savedTimeline.editor_state.compositionWidth;
            const savedHeight = savedTimeline.editor_state.compositionHeight;
            const orientationMatches = savedWidth === expectedSize.width && savedHeight === expectedSize.height;

            if (allCurrentInSaved && currentBlocks.length > 0 && staleBlocks.length === 0 && orientationMatches) {
              restoredState = savedTimeline.editor_state as UndoableState;
              console.log("RESTORED saved editor state:", {
                savedItemCount: Object.keys(items).length,
                currentBlockCount: currentBlocks.length,
                savedAt: savedTimeline.saved_at,
              });
            } else if (!orientationMatches) {
              console.log("Saved editor state has a stale output_format/canvas size — rebuilding fresh.", {
                saved: { width: savedWidth, height: savedHeight },
                expected: expectedSize,
              });
            } else if (staleBlocks.length > 0) {
              console.log("Saved editor state has implausibly short slots — rebuilding fresh from live TTS durations. Affected block_ids:", staleBlocks);
            } else {
              const missing = currentBlocks.filter((b: any) => !blockIdsInSaved.has(b.id)).map((b: any) => b.id);
              console.log("Saved editor state stale — rebuilding. Missing block_ids:", missing);
            }
          }
        } catch (err) {
          console.warn("Could not load saved timeline, building fresh:", err);
        }

        if (restoredState && !cancelled) {
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

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
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
      <div className="flex-1 min-h-0 overflow-hidden">
        <LuminacastEditor
          cast={cast}
          initialUndoableState={initialState}
          onUndoableStateChange={handleStateChange}
        />
      </div>
    </div>
  );
});
