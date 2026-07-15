/**
 * LayerOrderPanel — shows all items as a vertical stack (top = frontmost).
 * Drag to reorder z-index. Click to select.
 *
 * Phase 2.9.1 — initial layer panel
 * Phase 4.8.4 — visibility/mute toggle per layer, human-readable labels
 */
import React, { useMemo, useCallback, useState, useRef } from "react";
import {
  useAllItems,
  useTracks,
  useSelectedItems,
  useWriteContext,
} from "../utils/use-context";
import { setSelectedItems } from "../state/actions/set-selected-items";
import { hideTrack, unhideTrack } from "../state/actions/hide-track";
import { muteTrack, unmuteTrack } from "../state/actions/mute-track";
import type { EditorStarterItem } from "../items/item-type";
import type { TrackType } from "../state/types";
import {
  Image, Video, Music, Type, Square, Captions,
  ChevronUp, ChevronDown, ChevronsUp, ChevronsDown,
  Layers, Eye, EyeOff, Volume2, VolumeX,
} from "lucide-react";

function getItemIcon(type: string) {
  switch (type) {
    case "image":
      return <Image className="w-3.5 h-3.5 shrink-0" />;
    case "video":
      return <Video className="w-3.5 h-3.5 shrink-0" />;
    case "audio":
      return <Music className="w-3.5 h-3.5 shrink-0" />;
    case "text":
      return <Type className="w-3.5 h-3.5 shrink-0" />;
    case "solid":
      return <Square className="w-3.5 h-3.5 shrink-0" />;
    case "captions":
      return <Captions className="w-3.5 h-3.5 shrink-0" />;
    default:
      return <Layers className="w-3.5 h-3.5 shrink-0" />;
  }
}

function getItemLabel(item: EditorStarterItem): string {
  if (item.metadata?.block_id && item.metadata?.bonded) {
    const blockLabel = `Block ${item.metadata.block_id.slice(-4)}`;
    return item.type === "audio" ? `${blockLabel} (voice)` : `${blockLabel} (visual)`;
  }
  // Phase 4.8.4 — human-readable labels from item name/metadata
  if ((item as any).name) return (item as any).name;
  return `${item.type} ${item.id.slice(-4)}`;
}

interface FlatLayerItem {
  itemId: string;
  item: EditorStarterItem;
  trackId: string;
  trackIndex: number;
  itemIndex: number;
  track: TrackType;
}

/**
 * Build a flat ordered list of all items from tracks.
 * Order: first track first item = top/front, last track last item = bottom/back.
 * Phase 4.8.4: include hidden tracks (so user can toggle visibility back on).
 */
function buildFlatLayers(
  tracks: TrackType[],
  items: Record<string, EditorStarterItem>,
): FlatLayerItem[] {
  const layers: FlatLayerItem[] = [];
  for (let ti = 0; ti < tracks.length; ti++) {
    const track = tracks[ti];
    for (let ii = 0; ii < track.items.length; ii++) {
      const itemId = track.items[ii];
      const item = items[itemId];
      if (!item) continue;
      layers.push({ itemId, item, trackId: track.id, trackIndex: ti, itemIndex: ii, track });
    }
  }
  return layers;
}

export const LayerOrderPanel: React.FC = () => {
  const { items } = useAllItems();
  const { tracks } = useTracks();
  const { selectedItems } = useSelectedItems();
  const { setState } = useWriteContext();

  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const dragItemRef = useRef<number | null>(null);

  const flatLayers = useMemo(
    () => buildFlatLayers(tracks, items),
    [tracks, items],
  );

  const handleSelect = useCallback(
    (itemId: string) => {
      setState({
        update: (s) => setSelectedItems(s, [itemId]),
        commitToUndoStack: false,
      });
    },
    [setState],
  );

  const toggleTrackVisibility = useCallback(
    (trackId: string, isHidden: boolean, e: React.MouseEvent) => {
      e.stopPropagation();
      setState({
        update: (state) => isHidden ? unhideTrack(state, trackId) : hideTrack(state, trackId),
        commitToUndoStack: false,
      });
    },
    [setState],
  );

  const toggleTrackMute = useCallback(
    (trackId: string, isMuted: boolean, e: React.MouseEvent) => {
      e.stopPropagation();
      setState({
        update: (state) => isMuted ? unmuteTrack(state, trackId) : muteTrack(state, trackId),
        commitToUndoStack: true,
      });
    },
    [setState],
  );

  const moveItem = useCallback(
    (fromIdx: number, toIdx: number) => {
      if (fromIdx === toIdx || fromIdx < 0 || toIdx < 0) return;
      if (fromIdx >= flatLayers.length || toIdx >= flatLayers.length) return;

      const movingLayer = flatLayers[fromIdx];
      const targetLayer = flatLayers[toIdx];

      setState({
        update: (state) => {
          const newTracks = state.undoableState.tracks.map((t) => ({ ...t, items: [...t.items] }));

          // Remove item from its current track
          const srcTrack = newTracks.find((t) => t.id === movingLayer.trackId);
          if (!srcTrack) return state;
          const srcIdx = srcTrack.items.indexOf(movingLayer.itemId);
          if (srcIdx === -1) return state;
          srcTrack.items.splice(srcIdx, 1);

          // Insert into target track at target position
          const dstTrack = newTracks.find((t) => t.id === targetLayer.trackId);
          if (!dstTrack) return state;

          let dstIdx = dstTrack.items.indexOf(targetLayer.itemId);
          if (dstIdx === -1) dstIdx = dstTrack.items.length;
          if (toIdx > fromIdx) dstIdx += 1;

          dstTrack.items.splice(dstIdx, 0, movingLayer.itemId);

          return {
            ...state,
            undoableState: {
              ...state.undoableState,
              tracks: newTracks,
            },
          };
        },
        commitToUndoStack: true,
      });
    },
    [flatLayers, setState],
  );

  const handleDragStart = useCallback((idx: number) => {
    dragItemRef.current = idx;
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent, idx: number) => {
    e.preventDefault();
    setDragOverIndex(idx);
  }, []);

  const handleDrop = useCallback(
    (idx: number) => {
      if (dragItemRef.current !== null && dragItemRef.current !== idx) {
        moveItem(dragItemRef.current, idx);
      }
      dragItemRef.current = null;
      setDragOverIndex(null);
    },
    [moveItem],
  );

  const handleDragEnd = useCallback(() => {
    dragItemRef.current = null;
    setDragOverIndex(null);
  }, []);

  if (flatLayers.length === 0) {
    return (
      <div className="p-3 text-[11px] text-white/30 text-center">
        No layers on timeline
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <div className="px-3 py-2 text-[10px] font-medium text-white/40 uppercase tracking-wider border-b border-white/5">
        Layers ({flatLayers.length})
      </div>
      <div className="flex flex-col">
        {flatLayers.map((layer, idx) => {
          const isSelected = selectedItems.includes(layer.itemId);
          const isDragOver = dragOverIndex === idx;
          const isHidden = layer.track.hidden;
          const isMuted = layer.track.muted;

          return (
            <div
              key={layer.itemId}
              draggable
              onDragStart={() => handleDragStart(idx)}
              onDragOver={(e) => handleDragOver(e, idx)}
              onDrop={() => handleDrop(idx)}
              onDragEnd={handleDragEnd}
              onClick={() => handleSelect(layer.itemId)}
              className={`group flex items-center gap-2 px-3 py-1.5 cursor-pointer text-[11px] transition-colors border-b border-white/5 ${
                isSelected
                  ? "bg-accent/20 text-white"
                  : isHidden
                    ? "text-white/20 hover:bg-white/5"
                    : "text-white/60 hover:bg-white/5 hover:text-white/80"
              } ${isDragOver ? "border-t-2 border-t-accent" : ""}`}
            >
              <span className="cursor-grab text-white/20 hover:text-white/40">⠿</span>
              {getItemIcon(layer.item.type)}
              <span className={`truncate flex-1 ${isHidden ? "line-through opacity-50" : ""}`}>
                {getItemLabel(layer.item)}
              </span>
              {/* Phase 4.8.4 — visibility + mute toggles per layer */}
              <button
                onClick={(e) => toggleTrackVisibility(layer.trackId, isHidden, e)}
                className="p-0.5 text-white/30 hover:text-white/60"
                title={isHidden ? "Show layer" : "Hide layer"}
              >
                {isHidden ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
              </button>
              {layer.item.type === "audio" && (
                <button
                  onClick={(e) => toggleTrackMute(layer.trackId, isMuted, e)}
                  className="p-0.5 text-white/30 hover:text-white/60"
                  title={isMuted ? "Unmute" : "Mute"}
                >
                  {isMuted ? <VolumeX className="w-3 h-3" /> : <Volume2 className="w-3 h-3" />}
                </button>
              )}
              <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
                {idx > 0 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); moveItem(idx, 0); }}
                    className="p-0.5 text-white/30 hover:text-white/60"
                    title="Bring to front"
                  >
                    <ChevronsUp className="w-3 h-3" />
                  </button>
                )}
                {idx > 0 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); moveItem(idx, idx - 1); }}
                    className="p-0.5 text-white/30 hover:text-white/60"
                    title="Bring forward"
                  >
                    <ChevronUp className="w-3 h-3" />
                  </button>
                )}
                {idx < flatLayers.length - 1 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); moveItem(idx, idx + 1); }}
                    className="p-0.5 text-white/30 hover:text-white/60"
                    title="Send backward"
                  >
                    <ChevronDown className="w-3 h-3" />
                  </button>
                )}
                {idx < flatLayers.length - 1 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); moveItem(idx, flatLayers.length - 1); }}
                    className="p-0.5 text-white/30 hover:text-white/60"
                    title="Send to back"
                  >
                    <ChevronsDown className="w-3 h-3" />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
