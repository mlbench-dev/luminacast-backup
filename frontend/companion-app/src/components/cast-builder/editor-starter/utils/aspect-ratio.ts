import {EditorStarterAsset} from '../assets/assets';
import {EditorStarterItem} from '../items/item-type';

export const byDefaultKeepAspectRatioMap: Record<
	EditorStarterItem['type'],
	boolean
> = {
	image: true,
	gif: true,
	text: false,
	video: true,
	solid: false,
	audio: false,
	captions: false,
};

export const canKeepAspectRatioMap: Record<EditorStarterItem['type'], boolean> =
	{
		image: true,
		gif: true,
		video: true,
		solid: true,
		captions: false,
		audio: false,
		text: false,
	};

export const getKeepAspectRatio = (item: EditorStarterItem) => {
	if (
		item.type === 'captions' ||
		item.type === 'audio' ||
		item.type === 'text'
	) {
		return false;
	}

	if (
		item.type === 'gif' ||
		item.type === 'image' ||
		item.type === 'video' ||
		item.type === 'solid'
	) {
		return item.keepAspectRatio;
	}

	throw new Error(
		`Unhandled item type: ${JSON.stringify(item satisfies never)}`,
	);
};

const getAspectRatioFromAsset = (asset: EditorStarterAsset) => {
	if (asset.type === 'image') {
		return asset.width / asset.height;
	}

	if (asset.type === 'video') {
		return asset.width / asset.height;
	}

	if (asset.type === 'gif') {
		return asset.width / asset.height;
	}

	if (asset.type === 'caption') {
		return null;
	}

	if (asset.type === 'audio') {
		return null;
	}

	throw new Error(`Unhandled asset type: ${JSON.stringify(asset)}`);
};

export const getOriginalAspectRatio = ({
	item,
	asset,
}: {
	item: EditorStarterItem;
	asset: EditorStarterAsset | null;
}) => {
	const fromAsset = asset ? getAspectRatioFromAsset(asset) : null;

	if (fromAsset) {
		return fromAsset;
	}

	return item.width / item.height;
};
