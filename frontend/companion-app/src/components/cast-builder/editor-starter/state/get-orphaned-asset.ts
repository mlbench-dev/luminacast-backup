import {EditorStarterAsset} from '../assets/assets';
import {getAssetFromItem} from '../assets/utils';
import {EditorState} from './types';

export const getOrphanedAssetIds = ({
	assets,
	items,
}: {
	items: EditorState['undoableState']['items'];
	assets: EditorState['undoableState']['assets'];
}): EditorStarterAsset[] => {
	const usedAssets = new Set<string>();

	const itemIds = Object.keys(items);
	const assetIds = Object.keys(assets);

	for (const itemId of itemIds) {
		const item = items[itemId];
		const asset = getAssetFromItem({item, assets});

		if (asset) {
			usedAssets.add(asset.id);
		}
	}

	const orphanedAssets: EditorStarterAsset[] = [];
	for (const assetId of assetIds) {
		if (!usedAssets.has(assetId)) {
			orphanedAssets.push(assets[assetId]);
		}
	}

	return orphanedAssets;
};
