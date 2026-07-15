import {EditorStarterItem} from '../items/item-type';

export const filterSelectedItemstoOnlyReturnExistingItems = ({
	selectedItems,
	items,
}: {
	selectedItems: string[];
	items: Record<string, EditorStarterItem>;
}): string[] => {
	const copy = [...selectedItems];
	const keys = Object.keys(items);
	let changed = false;
	for (const selectedItem of selectedItems) {
		if (!keys.includes(selectedItem)) {
			copy.splice(copy.indexOf(selectedItem), 1);
			changed = true;
		}
	}
	return changed ? copy : selectedItems;
};
