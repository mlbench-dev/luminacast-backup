import {CanvasSnapPoint} from '../../canvas/snap/canvas-snap-types';
import {EditorState} from '../types';

export const applyCanvasSnapPoints = ({
	state,
	snapPoints,
}: {
	state: EditorState;
	snapPoints: CanvasSnapPoint[];
}): EditorState => {
	// Avoid unnecessary state updates if snap points haven't changed
	if (
		state.activeCanvasSnapPoints.length === 0 &&
		snapPoints.length === 0
	) {
		return state;
	}

	return {
		...state,
		activeCanvasSnapPoints: snapPoints,
	};
};

export const clearCanvasSnapPoints = (state: EditorState): EditorState => {
	if (state.activeCanvasSnapPoints.length === 0) {
		return state;
	}
	return {
		...state,
		activeCanvasSnapPoints: [],
	};
};
