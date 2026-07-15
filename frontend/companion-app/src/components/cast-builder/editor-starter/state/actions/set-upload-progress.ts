import {AssetUploadProgress, EditorStarterAsset} from '../../assets/assets';
import {EditorState} from '../types';

export const setUploadProgress = ({
	asset,
	uploadProgress,
	state,
}: {
	state: EditorState;
	asset: EditorStarterAsset;
	uploadProgress: AssetUploadProgress;
}): EditorState => {
	return {
		...state,
		assetStatus: {
			...state.assetStatus,
			[asset.id]: {
				type: 'in-progress',
				progress: uploadProgress,
			},
		},
	};
};
