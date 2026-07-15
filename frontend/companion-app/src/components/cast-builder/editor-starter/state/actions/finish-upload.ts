import {EditorStarterAsset} from '../../assets/assets';
import {EditorState} from '../types';

export const finishUpload = ({
	asset,
	remoteUrl: remoteUrl,
	state,
	remoteFileKey,
}: {
	state: EditorState;
	asset: EditorStarterAsset;
	remoteUrl: string;
	remoteFileKey: string;
}): EditorState => {
	const newAssets: Record<string, EditorStarterAsset> = {
		...state.undoableState.assets,
		[asset.id]: {
			...state.undoableState.assets[asset.id],
			remoteUrl,
			remoteFileKey,
		},
	};

	return {
		...state,
		undoableState: {
			...state.undoableState,
			assets: newAssets,
		},
		assetStatus: {
			...state.assetStatus,
			[asset.id]: {type: 'uploaded'},
		},
	};
};
