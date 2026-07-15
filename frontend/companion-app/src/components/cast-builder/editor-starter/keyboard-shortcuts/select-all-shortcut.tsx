import React, {useEffect} from 'react';
import {setSelectedItems} from '../state/actions/set-selected-items';
import {isEventTargetInputElement} from '../utils/is-event-target-input-element';
import {useAllItems, useWriteContext} from '../utils/use-context';

export const SelectAllShortcut: React.FC = () => {
	const {items} = useAllItems();
	const {setState} = useWriteContext();

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			// do not trigger if the target is an input
			if (isEventTargetInputElement(e)) {
				return;
			}

			// Select All: Cmd+A (Mac) or Ctrl+A (Windows/Linux)
			if ((e.metaKey || e.ctrlKey) && e.key === 'a') {
				e.preventDefault();

				// Get all item IDs
				const allItemIds = Object.keys(items);

				setState({
					update: (state) => setSelectedItems(state, allItemIds),
					commitToUndoStack: true,
				});
			}
		};

		window.addEventListener('keydown', handleKeyDown);

		return () => {
			window.removeEventListener('keydown', handleKeyDown);
		};
	}, [items, setState]);

	return null;
};
