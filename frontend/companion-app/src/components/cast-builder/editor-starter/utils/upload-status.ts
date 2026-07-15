import {AssetState} from '../assets/assets';
import {EditorState} from '../state/types';

export const isAssetUploading = (assetStatus: AssetState): boolean => {
	return (
		assetStatus.type === 'pending-upload' || assetStatus.type === 'in-progress'
	);
};

export const hasUploadingAssets = (
	assetStatus: Record<string, AssetState>,
): boolean => {
	return Object.values(assetStatus).some(isAssetUploading);
};

export const getUploadingAssetsCount = (state: EditorState): number => {
	return Object.values(state.assetStatus).filter(isAssetUploading).length;
};
