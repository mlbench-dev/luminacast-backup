import {getGifDurationInSeconds} from '@remotion/gif';
import {DropPosition} from '../../assets/add-asset';
import {GifAsset} from '../../assets/assets';
import {cacheAssetLocally} from '../../caching/indexeddb';
import {byDefaultKeepAspectRatioMap} from '../../utils/aspect-ratio';
import {type Dimensions} from '../../utils/detect-file-type';
import {calculateMediaDimensionsForCanvas} from '../../utils/dimension-utils';
import {generateRandomId} from '../../utils/generate-random-id';
import {GifItem} from './gif-item-type';

export const makeGifItem = async ({
	file,
	currentFrame,
	dropPosition,
	dimensions,
	assetId,
	blobUrl,
	fps,
	compositionWidth,
	compositionHeight,
	filename,
	remoteUrl,
	remoteFileKey,
}: {
	file: Blob;
	currentFrame: number;
	dropPosition: DropPosition | null;
	dimensions: Dimensions;
	assetId: string;
	blobUrl: string;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	remoteUrl: string | null;
	remoteFileKey: string | null;
	filename: string;
}): Promise<{item: GifItem; asset: GifAsset}> => {
	const id = generateRandomId();
	await cacheAssetLocally({
		assetId: id,
		value: file,
	});
	const duration = await getGifDurationInSeconds(blobUrl);

	const durationInFrames = Math.floor(duration * fps);

	const content = calculateMediaDimensionsForCanvas({
		mediaWidth: dimensions.width,
		mediaHeight: dimensions.height,
		containerWidth: compositionWidth,
		containerHeight: compositionHeight,
		dropPosition,
	});

	const asset: GifAsset = {
		id: assetId,
		type: 'gif',
		durationInSeconds: duration,
		filename,
		remoteUrl,
		remoteFileKey,
		size: file.size,
		mimeType: file.type,
		width: dimensions.width,
		height: dimensions.height,
	};

	const item: GifItem = {
		id,
		durationInFrames,
		gifStartFromInSeconds: 0,
		top: content.top,
		left: content.left,
		width: content.width,
		height: content.height,
		from: currentFrame,
		type: 'gif',
		opacity: 1,
		borderRadius: 0,
		rotation: 0,
		playbackRate: 1,
		assetId: asset.id,
		isDraggingInTimeline: false,
		keepAspectRatio: byDefaultKeepAspectRatioMap.gif,
		fadeInDurationInSeconds: 0,
		fadeOutDurationInSeconds: 0,
		cropLeft: 0,
		cropTop: 0,
		cropRight: 0,
		cropBottom: 0,
	};

	return {item, asset};
};
