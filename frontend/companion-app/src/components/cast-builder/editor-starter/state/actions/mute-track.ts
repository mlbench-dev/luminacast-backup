import {EditorState} from '../types';

export const muteTrack = (editorState: EditorState, trackId: string) => {
	let changed = false;

	const newTracks = editorState.undoableState.tracks.map((track) => {
		if (track.id !== trackId) {
			return track;
		}

		if (track.muted) {
			return track;
		}

		changed = true;
		return {...track, muted: true};
	});

	if (!changed) {
		return editorState;
	}

	return {
		...editorState,
		undoableState: {
			...editorState.undoableState,
			tracks: newTracks,
		},
	};
};

export const unmuteTrack = (editorState: EditorState, trackId: string) => {
	let changed = false;

	const newTracks = editorState.undoableState.tracks.map((track) => {
		if (track.id !== trackId) {
			return track;
		}

		if (!track.muted) {
			return track;
		}

		changed = true;
		return {...track, muted: false};
	});

	if (!changed) {
		return editorState;
	}

	return {
		...editorState,
		undoableState: {
			...editorState.undoableState,
			tracks: newTracks,
		},
	};
};
