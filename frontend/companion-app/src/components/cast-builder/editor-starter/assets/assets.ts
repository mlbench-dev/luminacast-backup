import {Caption} from '@remotion/captions';

export type AssetUploadProgress = {
	progress: number;
	loadedBytes: number;
	totalBytes: number;
};

export type AssetState =
	| {
			type: 'pending-upload';
	  }
	| {
			type: 'in-progress';
			progress: AssetUploadProgress;
	  }
	| {
			type: 'uploaded';
	  }
	| {
			type: 'error';
			error: Error;
			canRetry: boolean;
	  };

export type AssetUploadTask = {
	type: 'uploading';
	assetId: string;
	asset: EditorStarterAsset;
	status: AssetUploadProgress;
	startedAt: number;
	id: string;
};

type BaseAsset = {
	filename: string;
	id: string;
	size: number;
	remoteUrl: string | null;
	remoteFileKey: string | null;
	mimeType: string;
};

export type ImageAsset = BaseAsset & {
	type: 'image';
	width: number;
	height: number;
};

export type VideoAsset = BaseAsset & {
	type: 'video';
	durationInSeconds: number;
	hasAudioTrack: boolean;
	width: number;
	height: number;
};

export type GifAsset = BaseAsset & {
	type: 'gif';
	durationInSeconds: number;
	width: number;
	height: number;
};

export type AudioAsset = BaseAsset & {
	type: 'audio';
	durationInSeconds: number;
};

export type CaptionAsset = BaseAsset & {
	type: 'caption';
	captions: Caption[];
};

export type EditorStarterAsset =
	| ImageAsset
	| VideoAsset
	| GifAsset
	| AudioAsset
	| CaptionAsset;
