import {useCallback, useMemo, useRef, useSyncExternalStore} from 'react';
import {EditorState, UndoableState} from '../state/types';
import {filterSelectedItemstoOnlyReturnExistingItems} from './filter-selected-items-for-only-existing-items';

const MAX_HISTORY_SIZE = 50;

interface HistoryState {
	entries: UndoableState[];
	index: number;
}

const onServer = () => false;

export const useUndoRedo = (
	setState: React.Dispatch<React.SetStateAction<EditorState>>,
) => {
	const historyState = useRef<HistoryState>({
		entries: [],
		index: 0,
	});

	const listeners = useRef<(() => void)[]>([]);

	const isUndoRedoOperation = useRef(false);

	const pushHistory = useCallback(
		(stateSnapshot: UndoableState) => {
			const prevState = historyState.current;
			const lastEntry =
				historyState.current.entries[historyState.current.entries.length - 1];
			if (lastEntry && lastEntry === stateSnapshot) {
				return;
			}

			const truncated = prevState.entries.slice(0, prevState.index + 1);
			let newEntries = [...truncated, stateSnapshot];

			if (newEntries.length > MAX_HISTORY_SIZE) {
				newEntries = newEntries.slice(newEntries.length - MAX_HISTORY_SIZE);
			}

			historyState.current = {
				entries: newEntries,
				index: newEntries.length - 1,
			};
		},
		[historyState],
	);

	const undo = useCallback(() => {
		const prevState = historyState.current;
		if (prevState.index === 0) {
			return;
		}

		const newIndex = prevState.index - 1;
		const stateToRestore = prevState.entries[newIndex];

		if (stateToRestore) {
			isUndoRedoOperation.current = true;
			setState((prev) => {
				return {
					...prev,
					undoableState: stateToRestore,
					selectedItems: filterSelectedItemstoOnlyReturnExistingItems({
						selectedItems: prev.selectedItems,
						items: stateToRestore.items,
					}),
				};
			});
		}

		historyState.current = {
			...prevState,
			index: newIndex,
		};
		listeners.current.forEach((cb) => cb());
	}, [setState]);

	const redo = useCallback(() => {
		const prevState = historyState.current;
		if (prevState.index >= prevState.entries.length - 1) {
			return;
		}

		const newIndex = prevState.index + 1;
		const stateToRestore = prevState.entries[newIndex];

		if (stateToRestore) {
			isUndoRedoOperation.current = true;
			setState((prev) => ({
				...prev,
				undoableState: stateToRestore,
				selectedItems: filterSelectedItemstoOnlyReturnExistingItems({
					selectedItems: prev.selectedItems,
					items: stateToRestore.items,
				}),
			}));
		}

		historyState.current = {
			...prevState,
			index: newIndex,
		};
		listeners.current.forEach((cb) => cb());
	}, [setState]);

	const canUndoImperative = useCallback(() => {
		const prevState = historyState.current;
		return prevState.index > 0;
	}, []);

	const canRedoImperative = useCallback(() => {
		const prevState = historyState.current;
		return prevState.index < prevState.entries.length - 1;
	}, []);

	const canRedo = useSyncExternalStore(
		(cb) => {
			listeners.current.push(cb);
			return () => {
				listeners.current = listeners.current.filter((l) => l !== cb);
			};
		},
		canRedoImperative,
		onServer,
	);

	const canUndo = useSyncExternalStore(
		(cb) => {
			listeners.current.push(cb);
			return () => {
				listeners.current = listeners.current.filter((l) => l !== cb);
			};
		},
		canUndoImperative,
		onServer,
	);

	return useMemo(
		() => ({undo, redo, pushHistory, canUndo, canRedo}),
		[undo, redo, pushHistory, canUndo, canRedo],
	);
};
