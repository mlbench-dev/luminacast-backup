import {EditorStarterItem} from '../items/item-type';
import {findAssetById} from '../utils/find-asset-by-id';
import {CaptionAsset, EditorStarterAsset} from './assets';

export const getAssetStartInSeconds = (item: EditorStarterItem) => {
	if (item.type === 'audio') {
		return item.audioStartFromInSeconds;
	}

	if (item.type === 'video') {
		return item.videoStartFromInSeconds;
	}

	if (item.type === 'gif') {
		return item.gifStartFromInSeconds;
	}

	if (item.type === 'captions') {
		return item.captionStartInSeconds;
	}

	if (item.type === 'image') {
		return null;
	}

	if (item.type === 'text') {
		return null;
	}

	if (item.type === 'solid') {
		return null;
	}

	throw new Error('Invalid item type: ' + (item satisfies never));
};

export const getDurationInSecondsOfCaptionAsset = (asset: CaptionAsset) => {
	const lastCaption = asset.captions[asset.captions.length - 1];
	const durationInSeconds = lastCaption ? lastCaption.endMs / 1000 : 0;

	return durationInSeconds;
};

export const getAssetDurationInSeconds = (asset: EditorStarterAsset) => {
	if (asset.type === 'audio') {
		return asset.durationInSeconds;
	}

	if (asset.type === 'video') {
		return asset.durationInSeconds;
	}

	if (asset.type === 'gif') {
		return asset.durationInSeconds;
	}

	if (asset.type === 'caption') {
		return getDurationInSecondsOfCaptionAsset(asset);
	}

	if (asset.type === 'image') {
		return null;
	}

	throw new Error('Invalid asset type: ' + (asset satisfies never));
};

const getAssetMaxDurationInFrames = ({
	asset,
	fps,
}: {
	asset: EditorStarterAsset;
	fps: number;
}) => {
	const durationInSeconds = getAssetDurationInSeconds(asset);
	if (durationInSeconds === null) {
		return null;
	}
	return durationInSeconds * fps;
};

export const getAssetFromItem = ({
	item,
	assets,
}: {
	item: EditorStarterItem;
	assets: Record<string, EditorStarterAsset>;
}) => {
	const asset =
		'assetId' in item ? (findAssetById(assets, item.assetId) ?? null) : null;

	return asset;
};

export const getAssetMaxDurationInFramesFromItem = ({
	item,
	assets,
	fps,
	playbackRate,
}: {
	item: EditorStarterItem;
	assets: Record<string, EditorStarterAsset>;
	fps: number;
	playbackRate: number;
}) => {
	const asset = getAssetFromItem({item, assets});
	if (asset === null) {
		return null;
	}

	const durationInFrames = getAssetMaxDurationInFrames({asset, fps});
	if (durationInFrames === null) {
		return null;
	}

	return Math.round(durationInFrames / playbackRate);
};
