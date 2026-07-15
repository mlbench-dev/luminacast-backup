import {AudioAsset} from '../../assets/assets';
import {generateRandomId} from '../../utils/generate-random-id';
import {AudioItem} from './audio-item-type';

export const makeAudioItem = ({
	file,
	fps,
	durationInSeconds,
	currentFrame,
	size,
	assetId,
	filename,
	remoteUrl,
	remoteFileKey,
}: {
	file: Blob;
	filename: string;
	fps: number;
	durationInSeconds: number;
	size: number;
	currentFrame: number;
	assetId: string;
	remoteUrl: string | null;
	remoteFileKey: string | null;
}): {item: AudioItem; asset: AudioAsset} => {
	const id = generateRandomId();
	const durationInFrames = Math.floor(durationInSeconds * fps);

	const asset: AudioAsset = {
		id: assetId,
		type: 'audio',
		durationInSeconds,
		filename: filename,
		remoteUrl,
		remoteFileKey,
		size,
		mimeType: file.type,
	};

	const item: AudioItem = {
		id,
		durationInFrames,
		audioStartFromInSeconds: 0,
		from: currentFrame,
		type: 'audio',
		top: 0,
		left: 0,
		width: 100,
		height: 100,
		opacity: 1,
		decibelAdjustment: 0,
		playbackRate: 1,
		audioFadeInDurationInSeconds: 0,
		audioFadeOutDurationInSeconds: 0,
		assetId: asset.id,
		isDraggingInTimeline: false,
	};

	return {item, asset};
};
