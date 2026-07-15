import {DropPosition} from '../../assets/add-asset';
import {VideoAsset} from '../../assets/assets';
import {byDefaultKeepAspectRatioMap} from '../../utils/aspect-ratio';
import {calculateMediaDimensionsForCanvas} from '../../utils/dimension-utils';
import {generateRandomId} from '../../utils/generate-random-id';
import {VideoItem} from './video-item-type';

export const makeVideoItem = ({
	file,
	fps,
	compositionWidth,
	compositionHeight,
	durationInSeconds,
	dimensions,
	currentFrame,
	dropPosition,
	assetId,
	hasAudioTrack,
	filename,
	remoteUrl,
	remoteFileKey,
}: {
	file: Blob;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	durationInSeconds: number;
	dimensions: {width: number; height: number};
	currentFrame: number;
	dropPosition: DropPosition | null;
	assetId: string;
	hasAudioTrack: boolean;
	filename: string;
	remoteUrl: string | null;
	remoteFileKey: string | null;
}): {item: VideoItem; asset: VideoAsset} => {
	const id = generateRandomId();
	const durationInFrames = Math.floor(durationInSeconds * fps);

	const {width, height, top, left} = calculateMediaDimensionsForCanvas({
		mediaWidth: dimensions.width,
		mediaHeight: dimensions.height,
		containerWidth: compositionWidth,
		containerHeight: compositionHeight,
		dropPosition,
	});

	const asset: VideoAsset = {
		id: assetId,
		type: 'video',
		durationInSeconds,
		hasAudioTrack,
		filename: filename,
		remoteUrl,
		remoteFileKey,
		size: file.size,
		mimeType: file.type,
		width,
		height,
	};

	const item: VideoItem = {
		id,
		durationInFrames,
		videoStartFromInSeconds: 0,
		from: currentFrame,
		type: 'video',
		assetId: asset.id,
		isDraggingInTimeline: false,
		top,
		left,
		width,
		height,
		opacity: 1,
		borderRadius: 0,
		rotation: 0,
		decibelAdjustment: 0,
		playbackRate: 1,
		audioFadeInDurationInSeconds: 0,
		audioFadeOutDurationInSeconds: 0,
		fadeInDurationInSeconds: 0,
		fadeOutDurationInSeconds: 0,
		keepAspectRatio: byDefaultKeepAspectRatioMap.video,
		cropBottom: 0,
		cropTop: 0,
		cropLeft: 0,
		cropRight: 0,
	};

	return {item, asset};
};
