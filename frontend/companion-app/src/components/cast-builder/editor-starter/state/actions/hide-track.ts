import {EditorState} from '../types';

export const hideTrack = (editorState: EditorState, trackId: string) => {
	let changed = false;

	const newTracks = editorState.undoableState.tracks.map((track) => {
		if (track.id !== trackId) {
			return track;
		}

		if (track.hidden) {
			return track;
		}

		changed = true;
		return {...track, hidden: true};
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

export const unhideTrack = (editorState: EditorState, trackId: string) => {
	let changed = false;

	const newTracks = editorState.undoableState.tracks.map((track) => {
		if (track.id !== trackId) {
			return track;
		}

		if (!track.hidden) {
			return track;
		}

		changed = true;
		return {...track, hidden: false};
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
