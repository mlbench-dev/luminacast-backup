import {EditorState} from '../types';

/**
 * Per-ITEM visibility toggle — distinct from hideTrack/unhideTrack, which
 * hide every item on a shared track at once (see ItemMetadata.hidden's
 * docstring for why that distinction matters). Stored in metadata since
 * metadata is already preserved through every state mutation (spread
 * operator) without needing to touch each item-type union.
 */
export const toggleItemHidden = (editorState: EditorState, itemId: string): EditorState => {
	const item = editorState.undoableState.items[itemId];
	if (!item) return editorState;

	const nowHidden = !item.metadata?.hidden;

	return {
		...editorState,
		undoableState: {
			...editorState.undoableState,
			items: {
				...editorState.undoableState.items,
				[itemId]: {
					...item,
					metadata: {
						...item.metadata,
						hidden: nowHidden,
					},
				},
			},
		},
	};
};
