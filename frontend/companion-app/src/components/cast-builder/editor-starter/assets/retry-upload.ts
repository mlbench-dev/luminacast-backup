import {getObject} from '../caching/indexeddb';
import {SetState} from '../context-provider';
import {performAssetUpload} from '../utils/asset-upload-utils';
import {getUploadUrls} from '../utils/use-uploader';
import {EditorStarterAsset} from './assets';

export const retryAssetUpload = async ({
	asset,
	setState,
}: {
	asset: EditorStarterAsset;
	setState: SetState;
}) => {
	// Retrieve the cached file from IndexedDB
	const file = await getObject({key: asset.id});
	if (!file) {
		throw new Error('Cached file not found');
	}

	// Set status to pending first
	setState({
		update: (state) => {
			return {
				...state,
				assetStatus: {
					...state.assetStatus,
					[asset.id]: {
						type: 'pending-upload',
					},
				},
			};
		},
		commitToUndoStack: false,
	});

	// Try to get upload URLs
	const presignResultPromise = getUploadUrls(file);

	await performAssetUpload({setState, asset, presignResultPromise, file});
};
