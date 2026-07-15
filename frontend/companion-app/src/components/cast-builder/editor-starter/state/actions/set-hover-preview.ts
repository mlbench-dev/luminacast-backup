import {TextItemHoverPreview} from '../../items/text/override-text-item-with-hover-preview';
import {EditorState} from '../types';

export const setTextItemHoverPreview = ({
	state,
	hoverPreview,
}: {
	state: EditorState;
	hoverPreview: TextItemHoverPreview | null;
}): EditorState => {
	if (hoverPreview === state.textItemHoverPreview) {
		return state;
	}

	return {
		...state,
		textItemHoverPreview: hoverPreview,
	};
};
