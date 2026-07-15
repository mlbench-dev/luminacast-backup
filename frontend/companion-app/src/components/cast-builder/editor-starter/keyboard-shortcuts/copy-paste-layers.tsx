import {PlayerRef} from '@remotion/player';
import React, {useCallback, useEffect} from 'react';
import {addAsset} from '../assets/add-asset';
import {copyToClipboard} from '../clipboard/copy-to-clipboard';
import {parseItemsFromClipboardTextHtml} from '../clipboard/parse-items';
import {
	FEATURE_COPY_LAYERS,
	FEATURE_CUT_LAYERS,
	FEATURE_PASTE_ASSETS,
	FEATURE_PASTE_TEXT,
} from '../flags';
import {createTextItem} from '../items/text/create-text-item';
import {addItem} from '../state/actions/add-item';
import {cutItems} from '../state/actions/cut-items';
import {pasteItems} from '../state/actions/paste-items';
import {isEventTargetInputElement} from '../utils/is-event-target-input-element';
import {truthy} from '../utils/truthy';
import {useCurrentStateAsRef, useWriteContext} from '../utils/use-context';

export const CopyPasteLayers: React.FC<{
	playerRef: React.RefObject<PlayerRef | null>;
}> = ({playerRef}) => {
	const timelineWriteContext = useWriteContext();
	const {setState} = timelineWriteContext;
	const state = useCurrentStateAsRef();

	const performCopy = useCallback(
		async (e: ClipboardEvent) => {
			if (!e.clipboardData) {
				return;
			}

			const {selectedItems} = state.current;

			if (selectedItems.length === 0) {
				return;
			}

			const itemsToCopy = selectedItems
				.map((s) => state.current.undoableState.items[s])
				.filter(truthy);

			e.preventDefault();
			copyToClipboard(itemsToCopy);
		},
		[state],
	);

	const handleCopy = useCallback(
		async (e: ClipboardEvent) => {
			// Avoid interfering with native copy in input elements
			if (isEventTargetInputElement(e)) {
				return;
			}

			performCopy(e);
		},
		[performCopy],
	);

	const handleCut = useCallback(
		async (e: ClipboardEvent) => {
			// Avoid interfering with native cut in input elements
			if (isEventTargetInputElement(e)) {
				return;
			}

			const {selectedItems} = state.current;
			if (selectedItems.length === 0) {
				return;
			}

			performCopy(e);

			setState({
				update: (prev) => cutItems(prev, selectedItems),
				commitToUndoStack: true,
			});
		},
		[performCopy, setState, state],
	);

	const handlePaste = useCallback(
		async (e: ClipboardEvent) => {
			// Avoid overriding paste inside form fields
			if (isEventTargetInputElement(e)) {
				return;
			}
			if (!e.clipboardData) {
				return;
			}

			e.preventDefault();

			if (e.clipboardData.types.includes('text/html')) {
				const text = e.clipboardData.getData('text/html');
				const parsedAsItems = parseItemsFromClipboardTextHtml(text);
				if (parsedAsItems) {
					setState({
						update: (prev) =>
							pasteItems({
								state: prev,
								copiedItems: parsedAsItems,
								from: playerRef.current?.getCurrentFrame() ?? 0,
								position: null,
							}),
						commitToUndoStack: true,
					});
					return;
				}
			}
			const text = e.clipboardData.getData('text/plain');

			if (FEATURE_PASTE_TEXT && text.trim() !== '') {
				const {compositionWidth, compositionHeight} =
					state.current.undoableState;
				const item = await createTextItem({
					xOnCanvas: compositionWidth / 2,
					yOnCanvas: compositionHeight / 2,
					from: playerRef.current?.getCurrentFrame() ?? 0,
					text,
					align: 'center',
				});

				setState({
					update: (prev) =>
						addItem({
							state: prev,
							item,
							select: true,
							position: {type: 'front'},
						}),
					commitToUndoStack: true,
				});
				return;
			}

			const fps = state.current.undoableState.fps;
			const tracks = state.current.undoableState.tracks;

			const hasFiles = e.clipboardData.types.includes('Files');
			if (hasFiles && FEATURE_PASTE_ASSETS) {
				const {compositionHeight, compositionWidth} =
					state.current.undoableState;
				for (const file of e.clipboardData.files) {
					await addAsset({
						file,
						compositionHeight,
						compositionWidth,
						fps,
						tracks,
						timelineWriteContext: timelineWriteContext,
						playerRef,
						dropPosition: null,
						filename: file.name,
					});
				}
				return;
			}
		},
		[playerRef, setState, timelineWriteContext, state],
	);

	useEffect(() => {
		if (!FEATURE_COPY_LAYERS) {
			return;
		}

		document.addEventListener('copy', handleCopy);
		document.addEventListener('paste', handlePaste);

		return () => {
			document.removeEventListener('copy', handleCopy);
			document.removeEventListener('paste', handlePaste);
		};
	}, [handleCopy, handleCut, handlePaste]);

	useEffect(() => {
		if (!FEATURE_CUT_LAYERS) {
			return;
		}

		document.addEventListener('cut', handleCut);

		return () => {
			document.removeEventListener('cut', handleCut);
		};
	}, [handleCut]);

	return null;
};
