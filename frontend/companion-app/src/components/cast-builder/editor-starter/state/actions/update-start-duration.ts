import {EditorStarterItem} from '../../items/item-type';

export const updateAssetStartDurationOfItem = ({
	item,
	startDurationInSeconds,
}: {
	item: EditorStarterItem;
	startDurationInSeconds: number;
}) => {
	if (item.type === 'video') {
		if (item.videoStartFromInSeconds === startDurationInSeconds) {
			return item;
		}

		return {
			...item,
			videoStartFromInSeconds: startDurationInSeconds,
		};
	}

	if (item.type === 'audio') {
		if (item.audioStartFromInSeconds === startDurationInSeconds) {
			return item;
		}

		return {
			...item,
			audioStartFromInSeconds: startDurationInSeconds,
		};
	}

	if (item.type === 'gif') {
		if (item.gifStartFromInSeconds === startDurationInSeconds) {
			return item;
		}

		return {
			...item,
			gifStartFromInSeconds: startDurationInSeconds,
		};
	}

	if (item.type === 'captions') {
		if (item.captionStartInSeconds === startDurationInSeconds) {
			return item;
		}

		return {
			...item,
			captionStartInSeconds: startDurationInSeconds,
		};
	}

	if (item.type === 'image' || item.type === 'text' || item.type === 'solid') {
		return item;
	}

	throw new Error(`Invalid item type: ${JSON.stringify(item satisfies never)}`);
};
