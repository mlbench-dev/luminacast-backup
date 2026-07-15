import {EditorStarterAsset} from '../../assets/assets';
import {EditorState} from '../types';

export const addAssetToState = ({
	state,
	asset,
}: {
	state: EditorState;
	asset: EditorStarterAsset;
}): EditorState => {
	const newAssets = {
		...state.undoableState.assets,
		[asset.id]: asset,
	};

	return {
		...state,
		undoableState: {
			...state.undoableState,
			assets: newAssets,
		},
		assetStatus: {
			...state.assetStatus,
			[asset.id]: {
				type: 'pending-upload',
			},
		},
	};
};
