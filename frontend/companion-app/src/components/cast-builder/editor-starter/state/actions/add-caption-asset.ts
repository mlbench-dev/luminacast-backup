import {Caption} from '@remotion/captions';
import {CaptionAsset} from '../../assets/assets';
import {generateRandomId} from '../../utils/generate-random-id';
import {EditorState} from '../types';
import {addAssetToState} from './add-asset-to-state';

export const addCaptionAsset = ({
	state,
	captions,
	filename,
}: {
	state: EditorState;
	captions: Caption[];
	filename: string;
}): {state: EditorState; asset: CaptionAsset} => {
	const assetId = generateRandomId();

	const asset: CaptionAsset = {
		id: assetId,
		type: 'caption',
		captions,
		filename,
		remoteUrl: null,
		remoteFileKey: null,
		size: new Blob([JSON.stringify(captions)]).size,
		mimeType: 'application/json',
	};

	const newState = addAssetToState({state, asset: asset});

	// Caption assets are immediately uploaded (no actual upload needed)
	const stateWithUploadedStatus: EditorState = {
		...newState,
		assetStatus: {
			...newState.assetStatus,
			[asset.id]: {
				type: 'uploaded',
			},
		},
	};

	return {
		state: stateWithUploadedStatus,
		asset,
	};
};
