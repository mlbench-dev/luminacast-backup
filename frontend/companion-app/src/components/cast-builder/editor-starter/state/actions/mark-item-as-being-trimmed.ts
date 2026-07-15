import {ItemSide} from '../../items/trim-indicator';
import {EditorState} from '../types';

export const markItemAsBeingTrimmed = ({
	state,
	itemId,
	side,
	maxDurationInFrames,
	minFrom,
	trackIndex,
	top,
	height,
}: {
	state: EditorState;
	itemId: string;
	side: ItemSide;
	maxDurationInFrames: number | null;
	minFrom: number | null;
	trackIndex: number;
	top: number;
	height: number;
}): EditorState => {
	let currentItemsBeingTrimmed = state.itemsBeingTrimmed;

	for (const item of currentItemsBeingTrimmed) {
		if (item.itemId === itemId) {
			if (
				item.maxDurationInFrames === maxDurationInFrames &&
				item.minFrom === minFrom &&
				item.side === side
			) {
				// If already in this state, return old state
				return state;
			} else {
				// If item already was being trimmed in a different configuration, remove from array
				// so it can be re-added below
				currentItemsBeingTrimmed = currentItemsBeingTrimmed.filter(
					(i) => i.itemId !== itemId,
				);
			}
		}
	}

	return {
		...state,
		itemsBeingTrimmed: [
			...currentItemsBeingTrimmed,
			{itemId, side, maxDurationInFrames, minFrom, trackIndex, top, height},
		],
	};
};

export const unmarkItemAsBeingTrimmed = ({
	state,
	itemId,
}: {
	state: EditorState;
	itemId: string;
}): EditorState => {
	const currentItemsBeingTrimmed = state.itemsBeingTrimmed.find(
		(item) => item.itemId === itemId,
	);

	if (!currentItemsBeingTrimmed) {
		return state;
	}

	return {
		...state,
		itemsBeingTrimmed: state.itemsBeingTrimmed.filter(
			(item) => item.itemId !== itemId,
		),
	};
};
