import React, {useContext} from 'react';
import {Sequence} from 'remotion';
import {TextItemEditingContext} from '../context-provider';
import {EditModeContext} from '../edit-mode';
import {EditorStarterItem} from '../items/item-type';
import {useAllItems, useSelectedItems, useTracks} from '../utils/use-context';
import {SelectionOutline} from './selection-outline';

const hideOutlinesForItemTypes: EditorStarterItem['type'][] = ['audio'];

export const SortedOutlines: React.FC = () => {
	const {tracks} = useTracks();
	const {selectedItems: selectedItemIds} = useSelectedItems();
	const {items} = useAllItems();

	const allItems = React.useMemo(
		() =>
			tracks
				.filter((t) => !t.hidden)
				.map((t) => t.items)
				.flat(1)
				.filter((item) => !hideOutlinesForItemTypes.includes(items[item].type)),
		[items, tracks],
	);

	const selectedItems = React.useMemo(
		() => allItems.filter((item) => selectedItemIds.includes(item)),
		[allItems, selectedItemIds],
	);

	const editMode = useContext(EditModeContext);
	const textItemEditing = useContext(TextItemEditingContext);

	const itemsToDisplay = React.useMemo(() => {
		if (textItemEditing) {
			return [textItemEditing];
		}

		if (editMode.editMode === 'draw-solid') {
			return selectedItems;
		}

		return allItems;
	}, [editMode.editMode, selectedItems, allItems, textItemEditing]);

	return itemsToDisplay
		.slice()
		.reverse()
		.map((itemId) => {
			const item = items[itemId];
			return (
				<Sequence
					key={item.id}
					from={item.from}
					durationInFrames={item.durationInFrames}
					layout="none"
				>
					<SelectionOutline item={item} />
				</Sequence>
			);
		});
};
