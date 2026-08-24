/**
 * Phase 4.8.5 — Solo track: when soloed, only this track's audio plays.
 * Toggle: if already soloed, unsolo. If another is soloed, transfer solo.
 */
import type { EditorState, TrackType } from '../types';

/**
 * Whether `track`'s audio should actually be silent right now — its own
 * mute flag, OR some OTHER track is soloed (soloing one track implicitly
 * mutes every other track, same as flipping their mute switches). Used at
 * every render/preview call site instead of reading `track.muted` directly,
 * so Solo actually does what its label promises instead of just storing a
 * flag nothing reads.
 */
export function isTrackEffectivelyMuted(track: TrackType, allTracks: TrackType[]): boolean {
  if (track.muted) return true;
  const someoneElseSoloed = allTracks.some((t) => t.solo && t.id !== track.id);
  return someoneElseSoloed && !track.solo;
}

export const soloTrack = (state: EditorState, trackId: string): EditorState => {
  return {
    ...state,
    undoableState: {
      ...state.undoableState,
      tracks: state.undoableState.tracks.map((t) => ({
        ...t,
        solo: t.id === trackId ? true : false,
      })),
    },
  };
};

export const unsoloTrack = (state: EditorState, trackId: string): EditorState => {
  return {
    ...state,
    undoableState: {
      ...state.undoableState,
      tracks: state.undoableState.tracks.map((t) => ({
        ...t,
        solo: t.id === trackId ? false : t.solo,
      })),
    },
  };
};

export const unsoloAll = (state: EditorState): EditorState => {
  return {
    ...state,
    undoableState: {
      ...state.undoableState,
      tracks: state.undoableState.tracks.map((t) => ({
        ...t,
        solo: false,
      })),
    },
  };
};
