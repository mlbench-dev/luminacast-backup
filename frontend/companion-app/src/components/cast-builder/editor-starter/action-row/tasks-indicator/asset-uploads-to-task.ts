import {AssetUploadTask, EditorStarterAsset} from '../../assets/assets';
import {AssetStatusContext} from '../../context-provider';
import {truthy} from '../../utils/truthy';

export const assetContextToAssetTasks = ({
	assetStatus,
	assets,
}: {
	assetStatus: AssetStatusContext['assetStatus'];
	assets: Record<string, EditorStarterAsset>;
}): AssetUploadTask[] => {
	return Object.keys(assetStatus)
		.map((key): AssetUploadTask | null => {
			const status = assetStatus[key];
			if (status.type !== 'in-progress') {
				return null;
			}

			// Asset could have been deleted
			const asset = assets[key];
			if (!asset) {
				return null;
			}

			return {
				type: 'uploading',
				assetId: key,
				status: status.progress,
				asset,
				startedAt: Date.now(),
				id: key,
			};
		})
		.filter(truthy);
};
