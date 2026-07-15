import {EditorState} from '../types';

export const setSnappingEnabled = (
	state: EditorState,
	enabled: boolean,
): EditorState => {
	if (state.isSnappingEnabled === enabled) {
		return state;
	}
	return {
		...state,
		isSnappingEnabled: enabled,
		// Clear active snap point when disabling to avoid stale indicators
		activeSnapPoint: enabled ? state.activeSnapPoint : null,
	};
};

export const toggleSnapping = (state: EditorState): EditorState => {
	return setSnappingEnabled(state, !state.isSnappingEnabled);
};
