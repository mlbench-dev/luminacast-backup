import {Caption} from '@remotion/captions';
import {taskIndicatorRef} from '../action-row/tasks-indicator/tasks-indicator';
import {AudioAsset, VideoAsset} from '../assets/assets';
import {PresignResponse} from '../assets/types';
import {SetState} from '../context-provider';
import {
	addCaptioningTask,
	updateCaptioningTask,
} from '../state/actions/set-caption-state';
import {generateRandomId} from '../utils/generate-random-id';
import {uploadWithProgress} from '../utils/upload';
import {extractAudio} from './audio-buffer-to-wav';
import {GetCaptionsResponse} from './types';

function getAuthHeaders(): Record<string, string> {
	try {
		const raw = localStorage.getItem('luminacast-auth');
		if (raw) {
			const parsed = JSON.parse(raw);
			const token = parsed?.state?.token;
			if (token) {
				return {Authorization: `Bearer ${token}`};
			}
		}
	} catch {
		// ignore
	}
	return {};
}

export type CaptioningTaskStatus =
	| {
			type: 'extracting-audio';
			src: string;
	  }
	| {
			type: 'uploading-audio';
			progress: number;
			loadedBytes: number;
			totalBytes: number;
	  }
	| {
			type: 'captioning';
	  }
	| {
			type: 'error';
			error: Error;
	  }
	| {
			type: 'done';
			captions: Caption[];
			doneAt: number;
			captionItemId: string;
	  };

export type CaptioningTask = {
	id: string;
	assetId: string;
	filename: string;
	assetType: 'video' | 'audio';
	status: CaptioningTaskStatus;
	startedAt: number;
	type: 'captioning';
};

export const getCaptions = async ({
	src,
	setState,
	asset,
	captionItemId,
}: {
	src: string;
	setState: SetState;
	asset: AudioAsset | VideoAsset;
	captionItemId: string;
}) => {
	const taskId = generateRandomId();

	try {
		setState({
			commitToUndoStack: false,
			update: (prevState) => {
				return addCaptioningTask({
					state: prevState,
					newTask: {
						id: taskId,
						assetId: asset.id,
						filename: asset.filename,
						assetType: asset.type,
						status: {type: 'extracting-audio', src},
						startedAt: Date.now(),
						type: 'captioning',
					},
				});
			},
		});

		taskIndicatorRef.current?.open();

		const audio = await extractAudio(src);
		const audioFile = new File([audio], 'audio.wav', {
			type: 'audio/wav',
		});

		// Get a presigned URL for upload
		const presignResponse = await fetch('/api/upload', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				...getAuthHeaders(),
			},
			body: JSON.stringify({
				contentType: 'audio/wav',
				size: audio.byteLength,
			}),
		});

		if (!presignResponse.ok) {
			const errorData = await presignResponse.json();
			throw new Error(errorData.error || 'Failed to get upload URL');
		}

		const presignData = (await presignResponse.json()) as PresignResponse;

		// Upload the audio file to S3 with progress
		await uploadWithProgress({
			file: audioFile,
			url: presignData.presignedUrl,
			onProgress: ({progress, loadedBytes, totalBytes}) => {
				setState({
					commitToUndoStack: false,
					update: (prevState) => {
						return updateCaptioningTask({
							state: prevState,
							taskId,
							newStatus: {
								type: 'uploading-audio',
								progress,
								loadedBytes,
								totalBytes,
							},
						});
					},
				});
			},
		});

		setState({
			commitToUndoStack: false,
			update: (prevState) => {
				return updateCaptioningTask({
					state: prevState,
					taskId,
					newStatus: {type: 'captioning'},
				});
			},
		});

		// Request captions using the file key
		const res = await fetch(`/api/captions`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				...getAuthHeaders(),
			},
			body: JSON.stringify({
				fileKey: presignData.fileKey,
			}),
		});
		if (!res.ok) {
			throw new Error('Failed to get captions');
		}
		const json = (await res.json()) as GetCaptionsResponse;
		setState({
			commitToUndoStack: false,
			update: (prevState) => {
				return updateCaptioningTask({
					state: prevState,
					taskId,
					newStatus: {
						type: 'done',
						captions: json.captions,
						doneAt: Date.now(),
						captionItemId,
					},
				});
			},
		});
		return json.captions;
	} catch (err) {
		setState({
			commitToUndoStack: false,
			update: (prevState) => {
				return updateCaptioningTask({
					state: prevState,
					taskId,
					newStatus: {type: 'error', error: err as Error},
				});
			},
		});
	}
};
