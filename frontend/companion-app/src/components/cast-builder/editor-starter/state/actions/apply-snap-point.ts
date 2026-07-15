import {SnapPoint} from '../../timeline/utils/snap-points';
import {EditorState} from '../types';

export const applySnapPoint = ({
	state,
	snapPoint,
}: {
	state: EditorState;
	snapPoint: SnapPoint | null;
}): EditorState => {
	if (state.activeSnapPoint?.frame === snapPoint?.frame) {
		return state;
	}

	return {
		...state,
		activeSnapPoint: snapPoint,
	};
};
