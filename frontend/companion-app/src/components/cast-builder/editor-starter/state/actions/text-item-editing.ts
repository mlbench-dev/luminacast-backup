import {EditorState} from '../types';

export const markTextAsEditing = ({
	state,
	itemId,
}: {
	state: EditorState;
	itemId: string;
}): EditorState => {
	if (state.textItemEditing === itemId) {
		return state;
	}

	return {
		...state,
		textItemEditing: itemId,
	};
};

export const unmarkTextAsEditing = (state: EditorState): EditorState => {
	if (state.textItemEditing === null) {
		return state;
	}

	return {
		...state,
		textItemEditing: null,
	};
};
