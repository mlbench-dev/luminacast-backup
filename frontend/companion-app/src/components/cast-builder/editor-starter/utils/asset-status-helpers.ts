import {AssetState} from '../assets/assets';

/**
 * Type guard to check if asset status is 'uploaded'
 */
export const isAssetUploaded = (
	status: AssetState | undefined | null,
): status is {type: 'uploaded'} => {
	return status?.type === 'uploaded';
};

/**
 * Type guard to check if asset status is 'error'
 */
export const isAssetError = (
	status: AssetState | undefined | null,
): status is {
	type: 'error';
	error: Error;
	canRetry: boolean;
} => {
	return status?.type === 'error';
};

/**
 * Type guard to check if asset status is 'in-progress'
 */
export const isAssetUploading = (
	status: AssetState | undefined | null,
): status is {
	type: 'in-progress';
	progress: {
		progress: number;
		loadedBytes: number;
		totalBytes: number;
	};
} => {
	return status?.type === 'in-progress';
};

/**
 * Type guard to check if asset status is 'pending-upload'
 */
export const isAssetPendingUpload = (
	status: AssetState | undefined | null,
): status is {type: 'pending-upload'} => {
	return status?.type === 'pending-upload';
};

/**
 * Check if asset can be retried for upload
 */
export const canAssetRetryUpload = (
	status: AssetState | undefined | null,
): boolean => {
	if (isAssetError(status)) {
		return status.canRetry;
	}
	return false;
};
