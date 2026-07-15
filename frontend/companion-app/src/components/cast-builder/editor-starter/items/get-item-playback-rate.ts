import {EditorStarterItem} from './item-type';

export const getItemPlaybackRate = (item: EditorStarterItem) => {
	if (item.type === 'audio') {
		return item.playbackRate;
	}

	if (item.type === 'video') {
		return item.playbackRate;
	}

	if (item.type === 'gif') {
		return item.playbackRate;
	}

	if (item.type === 'image') {
		return 1;
	}

	if (item.type === 'text') {
		return 1;
	}

	if (item.type === 'solid') {
		return 1;
	}

	if (item.type === 'captions') {
		return 1;
	}

	throw new Error('Invalid item type: ' + (item satisfies never));
};
