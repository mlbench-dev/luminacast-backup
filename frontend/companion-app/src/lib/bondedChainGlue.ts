/**
 * Bonded chain glue — Phase 2.3
 *
 * When a bonded block's audio duration changes (via regeneration or variant switch),
 * all later bonded blocks shift in time to maintain continuity.
 *
 * Also handles:
 *  - Caption staleness (2.3.2)
 *  - Caption auto-regeneration tracking (2.6.4) — returns IDs of captions needing regen
 *  - Playhead snap (2.3.3) — handled at call site via Remotion player ref
 *  - Duration mode (2.7.3) — respects per-element crop/stretch/keep mode
 */
import type { UndoableState } from "@/components/cast-builder/editor-starter/state/types";
import type { EditorStarterItem } from "@/components/cast-builder/editor-starter/items/item-type";

/** Info about a caption that was marked stale and needs auto-regeneration */
export interface StaleCaptionInfo {
  captionItemId: string;
  sourceAudioId: string;
  blockId: string;
  lockedToSource: boolean;
}

export interface BondedShiftResult {
  state: UndoableState;
  /** Captions that were marked stale and have locked_to_source=true (need auto-regen) */
  staleCaptionsForRegen: StaleCaptionInfo[];
}

/**
 * Apply bonded chain shift after a block's audio duration changes.
 *
 * @param state       Current UndoableState
 * @param changedBlockId  The block_id whose audio changed
 * @param deltaFrames     Change in duration in frames (positive = grew, negative = shrank)
 * @param fps             Frames per second
 * @returns               BondedShiftResult with new state and stale caption info
 */
export function applyBondedShift(
  state: UndoableState,
  changedBlockId: string,
  deltaFrames: number,
  fps: number,
): UndoableState {
  return applyBondedShiftWithCaptions(state, changedBlockId, deltaFrames, fps).state;
}

/**
 * Same as applyBondedShift but also returns stale caption info for auto-regeneration.
 */
export function applyBondedShiftWithCaptions(
  state: UndoableState,
  changedBlockId: string,
  deltaFrames: number,
  fps: number,
): BondedShiftResult {
  if (deltaFrames === 0) return { state, staleCaptionsForRegen: [] };

  const items = state.items;
  const staleCaptionsForRegen: StaleCaptionInfo[] = [];

  // Find the changed block's bonded audio item to get its end frame
  let changedBlockEnd = 0;
  for (const item of Object.values(items)) {
    if (
      item.metadata?.block_id === changedBlockId &&
      item.metadata?.bonded
    ) {
      // Use the NEW end position (after duration update already applied to the audio item)
      const itemEnd = item.from + item.durationInFrames;
      if (itemEnd > changedBlockEnd) {
        changedBlockEnd = itemEnd;
      }
    }
  }

  // If we couldn't find the block, return unchanged
  if (changedBlockEnd === 0) return { state, staleCaptionsForRegen: [] };

  // The old end was changedBlockEnd - deltaFrames (since the audio item was already updated)
  const oldBlockEnd = changedBlockEnd - deltaFrames;

  const newItems: Record<string, EditorStarterItem> = {};
  let changed = false;

  for (const [id, item] of Object.entries(items)) {
    // Skip the changed block's own items (their duration was already updated)
    if (item.metadata?.block_id === changedBlockId) {
      newItems[id] = item;
      continue;
    }

    let updated = false;
    let newItem = item;

    // Bonded items that start at or after the old end of the changed block: shift
    if (item.metadata?.bonded && item.metadata?.block_id && item.from >= oldBlockEnd) {
      newItem = {
        ...newItem,
        from: newItem.from + deltaFrames,
      };
      updated = true;
    }

    // Non-bonded items linked to the changed block: shift with the block
    if (
      !item.metadata?.bonded &&
      item.metadata?.linked_to_block === changedBlockId
    ) {
      newItem = {
        ...newItem,
        from: newItem.from + deltaFrames,
      };
      updated = true;
    }

    // Phase 2.3.2 — Caption staleness
    // Phase 2.6.4 — Track captions needing auto-regeneration
    // Captions linked to the changed block's audio get marked stale
    if (item.type === "captions" && item.metadata?.source_audio_id) {
      // Check if the source_audio_id refers to an audio item of the changed block
      const sourceItem = items[item.metadata.source_audio_id];
      if (sourceItem?.metadata?.block_id === changedBlockId) {
        const lockedToSource = item.metadata?.locked_to_source !== false; // default true
        newItem = {
          ...newItem,
          metadata: {
            ...newItem.metadata,
            stale: true,
          },
        };
        updated = true;

        // Track for auto-regeneration if locked
        staleCaptionsForRegen.push({
          captionItemId: id,
          sourceAudioId: item.metadata.source_audio_id,
          blockId: sourceItem.metadata?.block_id ?? "",
          lockedToSource,
        });
      }
    }

    if (updated) {
      newItems[id] = newItem;
      changed = true;
    } else {
      newItems[id] = item;
    }
  }

  // Phase 2.7.3 — Update paired visual items respecting duration_mode
  for (const [id, item] of Object.entries(newItems)) {
    if (
      item.metadata?.block_id === changedBlockId &&
      item.metadata?.bonded &&
      (item.type === "video" || item.type === "image")
    ) {
      const durationMode = item.metadata?.duration_mode ?? "crop";
      const pairedAudioId = item.metadata?.paired_audio_element_id;
      if (!pairedAudioId || !newItems[pairedAudioId]) continue;

      const audioItem = newItems[pairedAudioId];

      if (durationMode === "keep") {
        // Leave visual element as-is — accept gap or overlap
        continue;
      }

      if (durationMode === "stretch" && item.type === "video") {
        // Adjust playback rate so video plays exactly the new audio duration
        // Original video frames stay the same, playback rate changes
        const originalDuration = item.durationInFrames;
        const targetDuration = audioItem.durationInFrames;
        if (originalDuration > 0 && targetDuration > 0 && originalDuration !== targetDuration) {
          // playbackRate = original / target: faster if target is shorter, slower if longer
          const currentRate = (item as any).playbackRate ?? 1;
          // The "original duration at rate 1" is item.durationInFrames * currentRate
          const originalAtRate1 = originalDuration * currentRate;
          const newRate = originalAtRate1 / targetDuration;
          newItems[id] = {
            ...item,
            durationInFrames: targetDuration,
            playbackRate: Math.round(newRate * 1000) / 1000,
          } as typeof item;
          changed = true;
        }
      } else {
        // crop: trim element end to match audio duration (default behavior)
        if (item.durationInFrames !== audioItem.durationInFrames) {
          newItems[id] = {
            ...item,
            durationInFrames: audioItem.durationInFrames,
          };
          changed = true;
        }
      }
    }
  }

  if (!changed) return { state, staleCaptionsForRegen: [] };

  return {
    state: {
      ...state,
      items: newItems,
    },
    staleCaptionsForRegen,
  };
}

/**
 * Phase 2.3.3 — Compute snapped playhead frame.
 *
 * If the playhead is inside a block that shrank below the playhead position,
 * snap to the new end of that block.
 *
 * @param currentFrame   Current playhead frame
 * @param changedBlockId The block that changed
 * @param items          Current items map
 * @param deltaFrames    Duration change in frames
 * @returns              New playhead frame, or null if no snap needed
 */
export function computePlayheadSnap(
  currentFrame: number,
  changedBlockId: string,
  items: Record<string, EditorStarterItem>,
  deltaFrames: number,
): number | null {
  // Only snap when block shrank
  if (deltaFrames >= 0) return null;

  // Find the changed block's bonded items to get its new bounds
  let blockStart = Infinity;
  let blockEnd = 0;
  for (const item of Object.values(items)) {
    if (
      item.metadata?.block_id === changedBlockId &&
      item.metadata?.bonded
    ) {
      blockStart = Math.min(blockStart, item.from);
      const end = item.from + item.durationInFrames;
      blockEnd = Math.max(blockEnd, end);
    }
  }

  // If playhead was within the block and now past its new end, snap to new end
  if (currentFrame >= blockStart && currentFrame > blockEnd) {
    return blockEnd;
  }

  return null;
}
