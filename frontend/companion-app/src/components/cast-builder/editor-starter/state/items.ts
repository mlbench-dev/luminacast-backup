import {FEATURE_AUDIO_WAVEFORM_FOR_VIDEO_ITEM} from '../flags';
import {EditorStarterItem} from '../items/item-type';
import {FILMSTRIP_HEIGHT_IF_THERE_IS_AUDIO} from '../timeline/timeline-item/timeline-item-film-strip/timeline-item-film-strip';
import {WAVEFORM_HEIGHT} from '../timeline/timeline-item/timeline-item-waveform/timeline-item-waveform';
import {TrackType} from './types';

const DEFAULT_TRACK_HEIGHT = 32;

const getItemHeight = (item: EditorStarterItem) => {
	if (item.type === 'video') {
		return (
			FILMSTRIP_HEIGHT_IF_THERE_IS_AUDIO +
			2 +
			(FEATURE_AUDIO_WAVEFORM_FOR_VIDEO_ITEM ? WAVEFORM_HEIGHT : 0)
		);
	}
	if (item.type === 'audio') {
		return FILMSTRIP_HEIGHT_IF_THERE_IS_AUDIO;
	}
	if (
		// Type safety check, add item types that don't have a waveform here
		item.type === 'captions' ||
		item.type === 'gif' ||
		item.type === 'text' ||
		item.type === 'solid' ||
		item.type === 'image'
	) {
		return DEFAULT_TRACK_HEIGHT;
	}

	throw new Error('Invalid item type: ' + (item satisfies never));
};

export const getTrackHeight = ({
	track,
	items,
}: {
	track: TrackType;
	items: Record<string, EditorStarterItem>;
}) => {
	if (track.items.length === 0) {
		return DEFAULT_TRACK_HEIGHT + TRACK_PADDING;
	}

	return (
		track.items.reduce(
			(acc, itemId) => Math.max(acc, getItemHeight(items[itemId])),
			0,
		) + TRACK_PADDING
	);
};

export const TRACK_DIVIDER_HEIGHT = 1;
export const TRACK_PADDING = 2;
