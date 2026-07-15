/**
 * Phase 4.8.5 — Delete a track and all its items from state.
 */
import type { EditorState } from '../types';

export const deleteTrack = (state: EditorState, trackId: string): EditorState => {
  const track = state.undoableState.tracks.find((t) => t.id === trackId);
  if (!track) return state;

  const itemsToRemove = new Set(track.items);

  const newItems = { ...state.undoableState.items };
  for (const itemId of itemsToRemove) {
    delete newItems[itemId];
  }

  return {
    ...state,
    undoableState: {
      ...state.undoableState,
      tracks: state.undoableState.tracks.filter((t) => t.id !== trackId),
      items: newItems,
    },
    selectedItems: state.selectedItems.filter((id) => !itemsToRemove.has(id)),
  };
};
