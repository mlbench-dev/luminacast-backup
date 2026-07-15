/* eslint-disable no-console */
import {toast} from 'sonner';
import {EditorStarterAsset} from '../assets/assets';
import {SetState} from '../context-provider';
import {finishUpload} from '../state/actions/finish-upload';
import {setUploadError} from '../state/actions/set-upload-error';
import {setUploadProgress} from '../state/actions/set-upload-progress';
import {PresignResult, uploadWithProgressUpdates} from './use-uploader';

export interface UploadResult {
	presignedUrl: string;
	readUrl: string;
	fileKey: string;
}

/**
 * Utility function to safely get error message from unknown error
 */
export const getErrorMessage = (error: unknown): string => {
	if (error instanceof Error) {
		return error.message;
	}
	return 'Unknown error occurred';
};

/**
 * Utility function to safely get error stack from unknown error
 */
export const getErrorStack = (error: unknown): string => {
	if (error instanceof Error) {
		return error.stack || error.message;
	}
	return 'Unknown error';
};

/**
 * Handles the upload attempt with progress tracking and error handling
 */
export const attemptAssetUpload = async ({
	uploadUrls,
	setState,
	asset,
	file,
}: {
	uploadUrls: UploadResult;
	setState: SetState;
	asset: EditorStarterAsset;
	file: Blob;
}): Promise<UploadResult> => {
	await uploadWithProgressUpdates(
		file,
		uploadUrls.presignedUrl,
		(uploadProgress) => {
			setState({
				update: (state) => {
					return setUploadProgress({state, asset, uploadProgress});
				},
				commitToUndoStack: false,
			});
		},
	);

	setState({
		update: (state) => {
			return finishUpload({
				state,
				asset,
				remoteUrl: uploadUrls.readUrl,
				remoteFileKey: uploadUrls.fileKey,
			});
		},
		commitToUndoStack: false,
	});

	return uploadUrls;
};

/**
 * Complete upload workflow - handles URL retrieval, upload attempt, and error states
 */
export const performAssetUpload = async ({
	setState,
	asset,
	presignResultPromise,
	file,
}: {
	setState: SetState;
	asset: EditorStarterAsset;
	presignResultPromise: Promise<PresignResult>;
	file: Blob;
}): Promise<void> => {
	const presignResult = await presignResultPromise;
	if (presignResult.type === 'file-too-large') {
		setState({
			update: (state) => {
				return setUploadError({
					state,
					asset: asset,
					error: new Error('File too large to upload'),
					canRetry: false,
				});
			},
			commitToUndoStack: false,
		});
		toast.error('File too large', {
			description: 'The file is too large to upload',
		});

		return;
	}

	if (presignResult.type === 'no-credentials') {
		setState({
			update: (state) => {
				return setUploadError({
					state,
					asset: asset,
					error: new Error(
						'AWS credentials not configured - https://www.remotion.dev/docs/editor-starter/asset-uploads',
					),
					canRetry: true,
				});
			},
			commitToUndoStack: false,
		});
		toast.error('No credentials', {
			description: 'Upload failed',
		});
		console.error(
			'AWS Asset upload failed. Did you set up AWS credentials? https://www.remotion.dev/docs/editor-starter/asset-uploads',
		);
		return;
	}

	if (presignResult.type === 'error') {
		setState({
			update: (state) => {
				return setUploadError({
					state,
					asset: asset,
					error: new Error(presignResult.error as string),
					canRetry: true,
				});
			},
			commitToUndoStack: false,
		});
		console.error(presignResult.error);
		toast.error('Upload failed');
		return;
	}

	try {
		// Attempt upload
		await attemptAssetUpload({
			uploadUrls: presignResult.result,
			setState,
			asset,
			file,
		});
	} catch (uploadError) {
		setState({
			update: (state) => {
				return setUploadError({
					state,
					asset: asset,
					error: uploadError as Error,
					canRetry: true,
				});
			},
			commitToUndoStack: false,
		});
		toast.error('Upload failed');
		console.error(uploadError);
	}
};
