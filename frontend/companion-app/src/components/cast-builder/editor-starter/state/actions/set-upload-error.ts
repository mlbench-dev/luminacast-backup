import {EditorStarterAsset} from '../../assets/assets';
import {EditorState} from '../types';

export const setUploadError = ({
	asset,
	error,
	canRetry = true,
	state,
}: {
	state: EditorState;
	asset: EditorStarterAsset;
	error: Error;
	canRetry: boolean;
}): EditorState => {
	return {
		...state,
		assetStatus: {
			...state.assetStatus,
			[asset.id]: {
				type: 'error',
				error,
				canRetry,
			},
		},
	};
};
