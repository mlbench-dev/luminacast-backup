import {EditorStarterAsset} from '../assets/assets';

export type AssetDownloadTask = {
	type: 'downloading';
	id: string;
	bytesRemaining: number;
	totalSize: number;
};

export const getDownloadTasks = ({
	downloadProgresses,
	assets,
	localUrls,
}: {
	downloadProgresses: Record<string, number | null>;
	assets: Record<string, EditorStarterAsset>;
	localUrls: Record<string, string>;
}): AssetDownloadTask | null => {
	const bytesRemaining = Object.keys(assets)
		.map((assetId): number => {
			if (localUrls[assetId]) {
				return 0;
			}
			const asset = assets[assetId];
			if (asset.type === 'caption') {
				return 0;
			}
			const value = downloadProgresses[assetId];
			if (value === null || value === undefined) {
				return asset.size;
			}

			return asset.size - value;
		})
		.reduce((acc, bytes) => acc + bytes, 0);

	if (bytesRemaining === 0) {
		return null;
	}

	const totalSize = Object.keys(assets)
		.map((assetId) => assets[assetId].size)
		.reduce((acc, size) => acc + size, 0);

	return {
		type: 'downloading',
		id: 'downloading',
		bytesRemaining,
		totalSize,
	};
};
