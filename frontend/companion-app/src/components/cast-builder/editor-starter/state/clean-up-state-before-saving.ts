import {AssetState, EditorStarterAsset} from '../assets/assets';
import {getAssetFromItem} from '../assets/utils';
import {EditorState, UndoableState} from './types';

export const cleanUpStateBeforeSaving = (
	state: UndoableState,
): UndoableState => {
	const newAssets: Record<string, EditorStarterAsset> = {};

	// Remove assets that are unused
	for (const item of Object.keys(state.items)) {
		const asset = getAssetFromItem({
			item: state.items[item],
			assets: state.assets,
		});
		if (asset) {
			newAssets[asset.id] = asset;
		}
	}

	return {
		...state,
		assets: newAssets,
	};
};

export const cleanUpAssetStatus = (state: EditorState): EditorState => {
	const usedAssetIds = new Set<string>();

	// Find all used asset IDs
	for (const item of Object.values(state.undoableState.items)) {
		const asset = getAssetFromItem({
			item,
			assets: state.undoableState.assets,
		});
		if (asset) {
			usedAssetIds.add(asset.id);
		}
	}

	// Keep only status for used assets
	const newAssetStatus: Record<string, AssetState> = {};
	for (const assetId of usedAssetIds) {
		if (state.assetStatus[assetId]) {
			newAssetStatus[assetId] = state.assetStatus[assetId];
		}
	}

	return {
		...state,
		assetStatus: newAssetStatus,
	};
};
