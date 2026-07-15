import {AudioItem} from '../../items/audio/audio-item-type';
import {CaptionsItem} from '../../items/captions/captions-item-type';
import {GifItem} from '../../items/gif/gif-item-type';
import {EditorStarterItem} from '../../items/item-type';
import {VideoItem} from '../../items/video/video-item-type';
import {
	AudioFadableItem,
	getCanFadeAudio,
	getCanFadeVisual,
	VisuallyFadableItem,
} from '../../utils/fade';
import {generateRandomId} from '../../utils/generate-random-id';
import {EditorState, TrackType} from '../types';

export const splitItem = ({
	state,
	idToSplit,
	framePosition,
}: {
	state: EditorState;
	idToSplit: string;
	framePosition: number;
}): EditorState => {
	// Find the track and item to split
	let targetTrack: TrackType | undefined;
	const targetItem: EditorStarterItem = state.undoableState.items[idToSplit];
	let targetTrackIndex = -1;

	for (let i = 0; i < state.undoableState.tracks.length; i++) {
		const track = state.undoableState.tracks[i];
		if (track.items.includes(idToSplit)) {
			targetTrack = track;
			targetTrackIndex = i;
			break;
		}
	}

	if (!targetTrack || !targetItem || targetTrackIndex === -1) {
		return state;
	}

	// Calculate the relative split position within the item
	const itemStart = targetItem.from;
	const itemEnd = itemStart + targetItem.durationInFrames;

	// Only split if the frame position is within the item bounds and not at the edges
	if (framePosition <= itemStart || framePosition >= itemEnd) {
		return state;
	}

	// Create the two new items
	const firstItemDuration = framePosition - itemStart;
	const secondItemDuration = itemEnd - framePosition;

	const firstItem: EditorStarterItem = {
		...targetItem,
		id: generateRandomId(),
		durationInFrames: firstItemDuration,
	};

	const secondItem: EditorStarterItem = {
		...targetItem,
		id: generateRandomId(),
		from: framePosition,
		durationInFrames: secondItemDuration,
	};

	// Handle special case for video - adjust videoStartFromInSeconds for the second item
	if (targetItem.type === 'video') {
		const firstItemDurationInSeconds =
			firstItemDuration / state.undoableState.fps;
		(secondItem as VideoItem).videoStartFromInSeconds =
			(targetItem.videoStartFromInSeconds || 0) + firstItemDurationInSeconds;
	}

	// Handle special case for audio - adjust audioStartFromInSeconds for the second item
	if (targetItem.type === 'audio') {
		const firstItemDurationInSeconds =
			firstItemDuration / state.undoableState.fps;
		(secondItem as AudioItem).audioStartFromInSeconds =
			(targetItem.audioStartFromInSeconds || 0) + firstItemDurationInSeconds;
	}

	// Handle special case for gif - adjust gifStartFromInSeconds for the second item
	if (targetItem.type === 'gif') {
		const firstItemDurationInSeconds =
			firstItemDuration / state.undoableState.fps;
		(secondItem as GifItem).gifStartFromInSeconds =
			(targetItem.gifStartFromInSeconds || 0) + firstItemDurationInSeconds;
	}

	// Handle special case for video - adjust videoStartFromInSeconds for the second item
	if (targetItem.type === 'audio') {
		const audioItem = targetItem as AudioItem;
		const firstItemDurationInSeconds =
			firstItemDuration / state.undoableState.fps;
		(secondItem as AudioItem).audioStartFromInSeconds =
			(audioItem.audioStartFromInSeconds || 0) + firstItemDurationInSeconds;
	}

	// Handle special case for captions - adjust captionStartInSeconds for the second item
	if (targetItem.type === 'captions') {
		const firstItemDurationInSeconds =
			firstItemDuration / state.undoableState.fps;
		(secondItem as CaptionsItem).captionStartInSeconds =
			(targetItem.captionStartInSeconds || 0) + firstItemDurationInSeconds;
	}

	// Special case for fadable items - keep fadein for first item and fade out for second item
	if (getCanFadeVisual(targetItem)) {
		(firstItem as VisuallyFadableItem).fadeOutDurationInSeconds = 0;
		(secondItem as VisuallyFadableItem).fadeInDurationInSeconds = 0;
		(firstItem as VisuallyFadableItem).fadeOutDurationInSeconds = Math.min(
			(firstItem as VisuallyFadableItem).fadeOutDurationInSeconds,
			firstItemDuration / state.undoableState.fps,
		);
		(secondItem as VisuallyFadableItem).fadeOutDurationInSeconds = Math.min(
			(secondItem as VisuallyFadableItem).fadeOutDurationInSeconds,
			secondItemDuration / state.undoableState.fps,
		);
	}

	// Special case for audio and video - keep volume fade in for first item and fade out for second item
	if (getCanFadeAudio(targetItem)) {
		(firstItem as AudioFadableItem).audioFadeOutDurationInSeconds = 0;
		(secondItem as AudioFadableItem).audioFadeInDurationInSeconds = 0;
		(firstItem as AudioFadableItem).audioFadeOutDurationInSeconds = Math.min(
			(firstItem as AudioFadableItem).audioFadeOutDurationInSeconds,
			firstItemDuration / state.undoableState.fps,
		);
		(secondItem as AudioFadableItem).audioFadeOutDurationInSeconds = Math.min(
			(secondItem as AudioFadableItem).audioFadeOutDurationInSeconds,
			secondItemDuration / state.undoableState.fps,
		);
	}

	// Return updated tracks with the split items
	const newTracks = state.undoableState.tracks.map(
		(track, index): TrackType => {
			if (index !== targetTrackIndex) {
				return track;
			}

			return {
				...track,
				items: [
					...track.items.filter((item) => item !== idToSplit),
					firstItem.id,
					secondItem.id,
				],
			};
		},
	);

	const newItems = {
		...state.undoableState.items,
		[firstItem.id]: firstItem,
		[secondItem.id]: secondItem,
	};

	delete newItems[idToSplit];

	return {
		...state,
		undoableState: {
			...state.undoableState,
			tracks: newTracks,
			items: newItems,
		},
		selectedItems: [secondItem.id],
	};
};
