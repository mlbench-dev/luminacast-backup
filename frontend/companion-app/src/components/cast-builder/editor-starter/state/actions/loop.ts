import {EditorState} from '../types';

export const setLoop = (state: EditorState, loop: boolean) => {
	if (state.loop === loop) {
		return state;
	}

	return {
		...state,
		loop,
	};
};
