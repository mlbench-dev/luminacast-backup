import {Dimensions} from '@remotion/layout-utils';
import {DropPosition} from '../../assets/add-asset';
import {ImageAsset} from '../../assets/assets';
import {cacheAssetLocally} from '../../caching/indexeddb';
import {byDefaultKeepAspectRatioMap} from '../../utils/aspect-ratio';
import {calculateMediaDimensionsForCanvas} from '../../utils/dimension-utils';
import {generateRandomId} from '../../utils/generate-random-id';
import {ImageItem} from './image-item-type';

export const makeImageItem = async ({
	file,
	currentFrame,
	dropPosition,
	assetId,
	fps,
	compositionWidth,
	compositionHeight,
	filename,
	remoteUrl,
	remoteFileKey,
	dimensions,
}: {
	file: Blob;
	currentFrame: number;
	dropPosition: DropPosition | null;
	assetId: string;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	remoteUrl: string | null;
	remoteFileKey: string | null;
	filename: string;
	dimensions: Dimensions;
}): Promise<{item: ImageItem; asset: ImageAsset}> => {
	const id = generateRandomId();
	const durationInFrames = fps * 2;
	await cacheAssetLocally({
		assetId: id,
		value: file,
	});

	const content = calculateMediaDimensionsForCanvas({
		mediaWidth: dimensions.width,
		mediaHeight: dimensions.height,
		containerWidth: compositionWidth,
		containerHeight: compositionHeight,
		dropPosition,
	});

	const asset: ImageAsset = {
		id: assetId,
		type: 'image',
		filename: filename,
		remoteUrl,
		remoteFileKey,
		size: file.size,
		mimeType: file.type,
		width: dimensions.width,
		height: dimensions.height,
	};

	const item: ImageItem = {
		id,
		durationInFrames,
		top: content.top,
		left: content.left,
		width: content.width,
		height: content.height,
		from: currentFrame,
		type: 'image',
		opacity: 1,
		borderRadius: 0,
		rotation: 0,
		assetId: asset.id,
		isDraggingInTimeline: false,
		keepAspectRatio: byDefaultKeepAspectRatioMap.image,
		fadeInDurationInSeconds: 0,
		fadeOutDurationInSeconds: 0,
		cropLeft: 0,
		cropTop: 0,
		cropRight: 0,
		cropBottom: 0,
	};

	return {item, asset};
};
