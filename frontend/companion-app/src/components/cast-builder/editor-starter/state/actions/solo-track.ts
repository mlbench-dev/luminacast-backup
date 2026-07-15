/**
 * Phase 4.8.5 — Solo track: when soloed, only this track's audio plays.
 * Toggle: if already soloed, unsolo. If another is soloed, transfer solo.
 */
import type { EditorState } from '../types';

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
