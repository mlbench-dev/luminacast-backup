import {EditorState} from '../types';
import {changeItem} from './change-item';

export const markAsDraggingInTimeline = (
	state: EditorState,
	itemId: string,
): EditorState => {
	return changeItem(state, itemId, (item) => {
		if (item.isDraggingInTimeline) {
			return item;
		}

		return {
			...item,
			isDraggingInTimeline: true,
		};
	});
};

export const unmarkAsDraggingInTimeline = (
	state: EditorState,
	itemId: string,
): EditorState => {
	return changeItem(state, itemId, (item) => {
		if (!item.isDraggingInTimeline) {
			return item;
		}

		return {
			...item,
			isDraggingInTimeline: false,
		};
	});
};

export const markMultipleAsDraggingInTimeline = (
	state: EditorState,
	itemIds: string[],
): EditorState => {
	let newState = state;
	for (const itemId of itemIds) {
		newState = markAsDraggingInTimeline(newState, itemId);
	}
	return newState;
};

export const unmarkMultipleAsDraggingInTimeline = (
	state: EditorState,
	itemIds: string[],
): EditorState => {
	let newState = state;
	for (const itemId of itemIds) {
		newState = unmarkAsDraggingInTimeline(newState, itemId);
	}
	return newState;
};
