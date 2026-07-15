import {AssetUploadProgress} from '../assets/assets';
import {PresignErrorResponse, PresignResponse} from '../assets/types';
import {UploadResult} from './asset-upload-utils';
import {uploadWithProgress} from './upload';

export type PresignResult =
	| {
			type: 'success';
			result: UploadResult;
	  }
	| {
			type: 'no-credentials';
	  }
	| {
			type: 'file-too-large';
	  }
	| {
			type: 'error';
			error: unknown;
	  };

export const getUploadUrls = async (file: Blob): Promise<PresignResult> => {
	const presignedResponse = await fetch('/api/upload', {
		method: 'POST',
		body: JSON.stringify({
			size: file.size,
			contentType: file.type,
		}),
		headers: {
			'Content-Type': 'application/json',
		},
	});

	if (!presignedResponse.ok) {
		if (presignedResponse.status === 404) {
			return {
				type: 'no-credentials',
			};
		}

		if (presignedResponse.status === 413) {
			return {
				type: 'file-too-large',
			};
		}

		const errorData = (await presignedResponse.json()) as PresignErrorResponse;
		return {
			type: 'error',
			error: errorData,
		};
	}

	const result = (await presignedResponse.json()) as PresignResponse;
	return {
		type: 'success',
		result,
	};
};

export const uploadWithProgressUpdates = async (
	file: Blob,
	presignedUrl: string,
	onProgress: (progress: AssetUploadProgress) => void,
): Promise<void> => {
	await uploadWithProgress({
		file,
		url: presignedUrl,
		onProgress,
	});
};
