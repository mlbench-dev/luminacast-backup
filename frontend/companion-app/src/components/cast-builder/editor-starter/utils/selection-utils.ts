import {EditorStarterItem} from '../items/item-type';

export const calculateSelectionAndDragState = ({
	clickedItem,
	currentSelectedItemIds,
	isMultiSelectMode,
}: {
	clickedItem: EditorStarterItem;
	currentSelectedItemIds: string[];
	isMultiSelectMode: boolean;
}): {allowDrag: boolean; newSelectedItems: string[]} => {
	const isItemAlreadySelected = currentSelectedItemIds.includes(clickedItem.id);

	if (isItemAlreadySelected && isMultiSelectMode) {
		return {
			allowDrag: false,
			newSelectedItems: currentSelectedItemIds.filter(
				(id) => id !== clickedItem.id,
			),
		};
	}
	if (isItemAlreadySelected) {
		return {allowDrag: true, newSelectedItems: currentSelectedItemIds};
	}

	if (isMultiSelectMode) {
		return {
			allowDrag: false,
			newSelectedItems: [...currentSelectedItemIds, clickedItem.id],
		};
	}
	return {allowDrag: true, newSelectedItems: [clickedItem.id]};
};

export const isMultiSelect = (e: React.MouseEvent<HTMLDivElement>) => {
	return e.metaKey || e.shiftKey;
};

export const adjustSelectionAfterClick = ({
	clickedItem,
	currentSelectedItemIds,
	isMultiSelectMode,
}: {
	clickedItem: EditorStarterItem;
	currentSelectedItemIds: string[];
	isMultiSelectMode: boolean;
}): string[] => {
	// If multi-select is OFF and multiple items are currently selected, reduce
	// the selection to the clicked item (assuming it was part of the selection).
	if (!isMultiSelectMode && currentSelectedItemIds.length > 1) {
		if (currentSelectedItemIds.includes(clickedItem.id)) {
			return [clickedItem.id];
		}
	}
	return currentSelectedItemIds;
};

export const MOVE_THRESHOLD_PX = 4;

export const hasExceededMoveThreshold = (
	startX: number,
	startY: number,
	currentX: number,
	currentY: number,
	threshold: number = MOVE_THRESHOLD_PX,
): boolean => {
	return (
		Math.abs(currentX - startX) >= threshold ||
		Math.abs(currentY - startY) >= threshold
	);
};
