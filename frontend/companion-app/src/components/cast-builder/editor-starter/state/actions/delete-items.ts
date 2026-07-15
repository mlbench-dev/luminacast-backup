import {removeEmptyTracks} from '../../utils/remove-empty-tracks';
import {getOrphanedAssetIds} from '../get-orphaned-asset';
import {EditorState, UndoableState} from '../types';
import {setSelectedItems} from './set-selected-items';

export const deleteItems = (state: EditorState, idsToDelete: string[]) => {
	const newTracks = state.undoableState.tracks.map((track) => {
		const items = track.items.filter((itemId) => {
			if (idsToDelete.includes(itemId)) {
				return false;
			}

			return true;
		});

		// Don't create a new object if no items were deleted
		if (items.length === track.items.length) {
			return track;
		}

		return {
			...track,
			items: items,
		};
	});

	const newState: UndoableState = {
		...state.undoableState,
		tracks: removeEmptyTracks(newTracks),
		items: {
			...state.undoableState.items,
		},
		assets: {
			...state.undoableState.assets,
		},
	};

	for (const id of idsToDelete) {
		delete newState.items[id];
	}

	const orphanedAssetIds = getOrphanedAssetIds({
		items: newState.items,
		assets: newState.assets,
	});

	for (const assetId of orphanedAssetIds) {
		delete newState.assets[assetId.id];
		const asset = state.undoableState.assets[assetId.id];
		if (!asset) {
			throw new Error('Asset not found');
		}

		const exists = state.undoableState.deletedAssets.find(
			(deletedAsset) => deletedAsset.assetId === assetId.id,
		);
		if (exists) {
			continue;
		}

		newState.deletedAssets.push({
			assetId: assetId.id,
			remoteUrl: asset.remoteUrl,
			remoteFileKey: asset.remoteFileKey,
			statusAtDeletion: state.assetStatus[assetId.id],
		});
	}

	const newSelectedItems = state.selectedItems.filter(
		(id) => !idsToDelete.includes(id),
	);

	return setSelectedItems(
		{
			...state,
			undoableState: newState,
			itemsBeingTrimmed: state.itemsBeingTrimmed.filter(
				(i) => !idsToDelete.includes(i.itemId),
			),
			itemSelectedForCrop: state.itemSelectedForCrop
				? idsToDelete.includes(state.itemSelectedForCrop)
					? null
					: state.itemSelectedForCrop
				: null,
		},
		newSelectedItems,
	);
};
