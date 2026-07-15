import {EditorStarterItem} from '../../items/item-type';
import {generateRandomId} from '../../utils/generate-random-id';
import {EditorState} from '../types';
import {addItem} from './add-item';
import {setSelectedItems} from './set-selected-items';

export const pasteItems = ({
	state,
	copiedItems,
	from,
	position,
}: {
	state: EditorState;
	copiedItems: EditorStarterItem[];
	from: number;
	position: null | {x: number; y: number};
}): EditorState => {
	let newState = state;
	const idsToSelect = [];

	// If we are adding multiple items, they should have the same horizontal offset
	// leftmost item as when they were copied.
	// But we insert where the playhead is, therefore we need to recalculate the offset
	const minFrom = Math.min(...copiedItems.map((item) => item.from));
	const offsetFrames = from - minFrom;

	// since we're adding items to the top of the timeline (position: { type: 'front' })
	// we need to reverse the order of the copied items
	// to maintain the correct layer order
	for (const copiedItem of [...copiedItems].reverse()) {
		// Preserve original from value but add offset
		const finalItem: EditorStarterItem = {
			...copiedItem,
			id: generateRandomId(),
			from: copiedItem.from + offsetFrames,
		};

		// If position is provided, update the left and top coordinates
		// Subtract half width/height to center the item at the clicked position
		if (position) {
			finalItem.left = position.x - finalItem.width / 2;
			finalItem.top = position.y - finalItem.height / 2;
		}

		newState = addItem({
			state: newState,
			item: finalItem,
			select: false,
			position: {type: 'front'},
		});

		idsToSelect.push(finalItem.id);
	}

	return setSelectedItems(newState, idsToSelect);
};
