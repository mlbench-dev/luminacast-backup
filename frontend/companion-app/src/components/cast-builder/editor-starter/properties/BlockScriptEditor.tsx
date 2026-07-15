/**
 * BlockScriptEditor — properties panel section for bonded audio elements.
 *
 * When user selects a bonded audio element (metadata.block_id exists), shows:
 *  - Script text textarea (pre-filled from current variant's script_text)
 *  - Regenerate audio button (with loading spinner)
 *  - Variants list (collapsible, shows all variants for this block)
 *
 * Phase 2.2.1 / 2.2.3 / 2.2.4
 */
import React, { memo, useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, ChevronDown, ChevronRight, Check } from "lucide-react";
import { toast } from "sonner";
import { castsApi } from "@/lib/api";
import type { AudioItem } from "../items/audio/audio-item-type";
import { changeItem } from "../state/actions/change-item";
import { addCaptionAsset } from "../state/actions/add-caption-asset";
import { useWriteContext, useAllItems, useAssets, useFps } from "../utils/use-context";
import { useLuminacastEditor } from "../luminacast-context";
import {
  CollapsableInspectorSection,
  InspectorDivider,
} from "../inspector/components/inspector-section";
import { InspectorLabel, InspectorSubLabel } from "../inspector/components/inspector-label";
import { applyBondedShiftWithCaptions } from "@/lib/bondedChainGlue";
import {
  regenerateCaptionForBlock,
  applyCaptionResultsToState,
} from "@/lib/captionGeneration";

interface BlockVariant {
  variant_id: string;
  script_text: string;
  audio_url: string;
  duration_seconds: number;
  is_active: boolean;
  created_at: string;
}

const BlockScriptEditorUnmemoized: React.FC<{
  item: AudioItem;
}> = ({ item }) => {
  const { castId, cast } = useLuminacastEditor();
  const { setState } = useWriteContext();
  const { items } = useAllItems();
  const { assets } = useAssets();
  const { fps } = useFps();
  const blockId = item.metadata?.block_id;

  // Find the current block's script_text from cast data
  const currentBlock = cast.blocks?.find((b: any) => b.id === blockId);
  const activeVariant = currentBlock?.variants?.find(
    (v: any) => v.id === item.metadata?.variant_id,
  ) ?? currentBlock?.variants?.[0];

  const [scriptText, setScriptText] = useState(activeVariant?.script_text ?? "");
  const [regenerating, setRegenerating] = useState(false);
  const [variants, setVariants] = useState<BlockVariant[]>([]);
  const [variantsOpen, setVariantsOpen] = useState(false);
  const [loadingVariants, setLoadingVariants] = useState(false);

  // Load variants when section is opened
  useEffect(() => {
    if (variantsOpen && blockId) {
      setLoadingVariants(true);
      castsApi
        .listBlockVariants(castId, blockId)
        .then(setVariants)
        .catch((err) => {
          console.error("Failed to load variants:", err);
          toast.error("Failed to load variants");
        })
        .finally(() => setLoadingVariants(false));
    }
  }, [variantsOpen, castId, blockId]);

  /**
   * Swap audio in place on the timeline after regeneration or variant switch.
   * Phase 2.2.3 + 2.2.4
   */
  const swapAudioInPlace = useCallback(
    (audioUrl: string, durationSeconds: number, variantId: string) => {
      if (!blockId) return;

      setState({
        update: (state) => {
          const oldItem = state.undoableState.items[item.id] as AudioItem;
          if (!oldItem) return state;

          const oldDurationFrames = oldItem.durationInFrames;
          const newDurationFrames = Math.round(durationSeconds * fps);
          const deltaFrames = newDurationFrames - oldDurationFrames;

          // Find or create the audio asset and update its URL
          const assetId = oldItem.assetId;
          const newAssets = { ...state.undoableState.assets };
          if (newAssets[assetId]) {
            newAssets[assetId] = {
              ...newAssets[assetId],
              remoteUrl: audioUrl,
              ...(newAssets[assetId].type === "audio"
                ? { durationInSeconds: durationSeconds }
                : {}),
            };
          }

          // Update the audio item's duration and metadata.variant_id
          let newState = {
            ...state,
            undoableState: {
              ...state.undoableState,
              assets: newAssets,
            },
          };

          newState = changeItem(newState, item.id, (i) => ({
            ...i,
            durationInFrames: newDurationFrames,
            metadata: {
              ...i.metadata,
              variant_id: variantId,
            },
          }));

          // Phase 2.3 — apply bonded chain shift if duration changed
          // Phase 2.6.4 — also get stale captions for auto-regeneration
          if (deltaFrames !== 0) {
            const shiftResult = applyBondedShiftWithCaptions(
              newState.undoableState,
              blockId,
              deltaFrames,
              fps,
            );
            newState = {
              ...newState,
              undoableState: shiftResult.state,
            };

            // Phase 2.6.4 — trigger async caption auto-regeneration for locked captions
            const lockedCaptions = shiftResult.staleCaptionsForRegen.filter(
              (c) => c.lockedToSource,
            );
            if (lockedCaptions.length > 0) {
              // Fire-and-forget: regenerate captions in the background
              // We don't await this — the UI updates immediately, captions regen async
              setTimeout(() => {
                triggerCaptionAutoRegen(lockedCaptions);
              }, 0);
            }
          }

          return newState;
        },
        commitToUndoStack: true,
      });
    },
    [blockId, item.id, fps, setState],
  );

  /**
   * Phase 2.6.4 — Trigger caption auto-regeneration for locked captions.
   * Runs asynchronously after bonded shift completes.
   */
  const triggerCaptionAutoRegen = useCallback(
    async (staleCaptions: { captionItemId: string; sourceAudioId: string; blockId: string }[]) => {
      for (const staleCaption of staleCaptions) {
        try {
          // Find the source audio item to get its URL
          const sourceAudioItem = items[staleCaption.sourceAudioId];
          if (!sourceAudioItem || sourceAudioItem.type !== "audio") continue;

          const audioAsset = assets[(sourceAudioItem as AudioItem).assetId];
          const audioUrl = audioAsset?.remoteUrl;
          if (!audioUrl) continue;

          const result = await regenerateCaptionForBlock(
            castId,
            sourceAudioItem as AudioItem,
            audioUrl,
            staleCaption.blockId,
            fps,
          );

          if (result && result.captions.length > 0) {
            // Replace existing caption item in place
            setState({
              update: (state) => {
                const { state: stateWithAsset, asset: captionAsset } =
                  addCaptionAsset({
                    state,
                    captions: result.captions,
                    filename: `captions_${staleCaption.blockId}.srt`,
                  });

                // Update existing caption item with new asset and clear stale flag
                const existingCaption = stateWithAsset.undoableState.items[staleCaption.captionItemId];
                if (!existingCaption) return stateWithAsset;

                return {
                  ...stateWithAsset,
                  undoableState: {
                    ...stateWithAsset.undoableState,
                    items: {
                      ...stateWithAsset.undoableState.items,
                      [staleCaption.captionItemId]: {
                        ...existingCaption,
                        assetId: captionAsset.id,
                        metadata: {
                          ...existingCaption.metadata,
                          stale: false,
                        },
                      },
                    },
                  },
                };
              },
              commitToUndoStack: true,
            });
          }
        } catch (err) {
          console.error("Caption auto-regen failed for block", staleCaption.blockId, err);
          // Don't toast — stale indicator remains, user can manually regenerate
        }
      }
    },
    [castId, items, assets, fps, setState],
  );

  /** Phase 2.2.3 — Regenerate audio */
  const handleRegenerate = useCallback(async () => {
    if (!blockId || regenerating) return;

    setRegenerating(true);
    try {
      const result = await castsApi.regenerateBlockAudio(castId, blockId, scriptText);
      swapAudioInPlace(result.audio_url, result.duration_seconds, result.variant_id);
      toast.success("Audio regenerated");

      // Refresh variants list if open
      if (variantsOpen) {
        castsApi.listBlockVariants(castId, blockId).then(setVariants).catch(() => {});
      }
    } catch (err) {
      console.error("Regeneration failed:", err);
      toast.error("Audio regeneration failed");
    } finally {
      setRegenerating(false);
    }
  }, [blockId, castId, scriptText, regenerating, swapAudioInPlace, variantsOpen]);

  /** Phase 2.2.4 — Select a variant */
  const handleSelectVariant = useCallback(
    async (variantId: string) => {
      if (!blockId) return;
      try {
        const result = await castsApi.selectBlockVariant(castId, blockId, variantId);
        swapAudioInPlace(result.audio_url, result.duration_seconds, result.variant_id);

        // Update variants list to reflect new active state
        setVariants((prev) =>
          prev.map((v) => ({
            ...v,
            is_active: v.variant_id === variantId,
          })),
        );
        toast.success("Variant selected");
      } catch (err) {
        console.error("Failed to select variant:", err);
        toast.error("Failed to select variant");
      }
    },
    [blockId, castId, swapAudioInPlace],
  );

  if (!blockId) return null;

  return (
    <>
      <InspectorDivider />
      <CollapsableInspectorSection
        summary={<InspectorLabel>Script &amp; Audio</InspectorLabel>}
        id={`block-script-${item.id}`}
        defaultOpen
      >
        {/* Script text textarea */}
        <InspectorSubLabel>Script Text</InspectorSubLabel>
        <textarea
          className="w-full rounded border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/30 focus:outline-none resize-y"
          rows={4}
          value={scriptText}
          onChange={(e) => setScriptText(e.target.value)}
          placeholder="Enter script text..."
          disabled={regenerating}
        />
        <div className="mt-1 text-right text-[0.65rem] text-white/40">
          {scriptText.length}/280 chars
        </div>

        {/* Regenerate button */}
        <button
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={handleRegenerate}
          disabled={regenerating || !scriptText.trim()}
        >
          {regenerating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          {regenerating ? "Regenerating..." : "Regenerate Audio"}
        </button>
      </CollapsableInspectorSection>

      <InspectorDivider />

      {/* Variants list (collapsible) */}
      <div className="py-2">
        <button
          className="flex w-full items-center px-4 text-xs font-bold text-neutral-300"
          onClick={() => setVariantsOpen((o) => !o)}
        >
          {variantsOpen ? (
            <ChevronDown className="mr-1 h-3 w-3" />
          ) : (
            <ChevronRight className="mr-1 h-3 w-3" />
          )}
          Variants
        </button>

        {variantsOpen && (
          <div className="mt-1 px-4">
            {loadingVariants ? (
              <div className="flex items-center gap-1 py-2 text-xs text-white/40">
                <Loader2 className="h-3 w-3 animate-spin" />
                Loading...
              </div>
            ) : variants.length === 0 ? (
              <div className="py-2 text-xs text-white/40">No variants found</div>
            ) : (
              <div className="flex flex-col gap-1">
                {variants.map((v) => (
                  <button
                    key={v.variant_id}
                    className={`flex items-start gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors ${
                      v.is_active
                        ? "bg-blue-600/20 text-blue-300"
                        : "text-white/70 hover:bg-white/5"
                    }`}
                    onClick={() => {
                      if (!v.is_active) handleSelectVariant(v.variant_id);
                    }}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="truncate">
                        {v.script_text?.slice(0, 60) || "(empty)"}
                        {(v.script_text?.length ?? 0) > 60 ? "..." : ""}
                      </div>
                      <div className="mt-0.5 text-[0.65rem] text-white/40">
                        {v.duration_seconds?.toFixed(1)}s
                      </div>
                    </div>
                    {v.is_active && (
                      <span className="mt-0.5 flex items-center gap-0.5 rounded bg-blue-600/30 px-1.5 py-0.5 text-[0.6rem] font-medium text-blue-300">
                        <Check className="h-2.5 w-2.5" />
                        Active
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
};

export const BlockScriptEditor = memo(BlockScriptEditorUnmemoized);
