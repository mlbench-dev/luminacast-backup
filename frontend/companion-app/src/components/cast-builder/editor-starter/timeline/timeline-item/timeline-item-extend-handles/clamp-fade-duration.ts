import {EditorStarterItem} from '../../../items/item-type';
import {FADE_HANDLE_WIDTH} from '../timeline-item-fade-control/fade-drag-handles';

export type PreferredFadeSide = 'in' | 'out';

export const REQUIRED_WIDTH_BETWEEN_FADE_HANDLES = 1;

export const clampFadeDurations = ({
	item,
	fps,
	preferSide,
	pixelsPerFrame,
}: {
	item: EditorStarterItem;
	fps: number;
	preferSide: PreferredFadeSide;
	pixelsPerFrame: number;
}): EditorStarterItem => {
	const itemDurationInSeconds = item.durationInFrames / fps;

	// create a gap between the fade handles after clamping
	const totalGapPx = FADE_HANDLE_WIDTH + REQUIRED_WIDTH_BETWEEN_FADE_HANDLES;
	const minGapInSeconds = totalGapPx / (pixelsPerFrame * fps);

	const clampPair = (
		fadeIn: number,
		fadeOut: number,
		prefer: PreferredFadeSide,
	): {in: number; out: number} => {
		const safeIn = Math.max(0, Math.min(fadeIn, itemDurationInSeconds));
		const safeOut = Math.max(0, Math.min(fadeOut, itemDurationInSeconds));

		if (prefer === 'out') {
			const clampedOut = Math.max(
				0,
				Math.min(
					safeOut,
					Math.max(0, itemDurationInSeconds - safeIn - minGapInSeconds),
				),
			);
			const clampedIn = Math.max(0, Math.min(safeIn, itemDurationInSeconds));
			return {in: clampedIn, out: clampedOut};
		}

		// prefer 'in'
		const clampedIn = Math.max(
			0,
			Math.min(
				safeIn,
				Math.max(0, itemDurationInSeconds - safeOut - minGapInSeconds),
			),
		);
		const clampedOut = Math.max(0, Math.min(safeOut, itemDurationInSeconds));
		return {in: clampedIn, out: clampedOut};
	};

	if (item.type === 'audio') {
		const {in: audioIn, out: audioOut} = clampPair(
			item.audioFadeInDurationInSeconds,
			item.audioFadeOutDurationInSeconds,
			preferSide,
		);

		if (
			audioIn === item.audioFadeInDurationInSeconds &&
			audioOut === item.audioFadeOutDurationInSeconds
		) {
			return item;
		}

		return {
			...item,
			audioFadeInDurationInSeconds: audioIn,
			audioFadeOutDurationInSeconds: audioOut,
		};
	}

	if (item.type === 'video') {
		const {in: videoIn, out: videoOut} = clampPair(
			item.fadeInDurationInSeconds,
			item.fadeOutDurationInSeconds,
			preferSide,
		);

		const {in: audioIn, out: audioOut} = clampPair(
			item.audioFadeInDurationInSeconds,
			item.audioFadeOutDurationInSeconds,
			preferSide,
		);

		if (
			videoIn === item.fadeInDurationInSeconds &&
			videoOut === item.fadeOutDurationInSeconds &&
			audioIn === item.audioFadeInDurationInSeconds &&
			audioOut === item.audioFadeOutDurationInSeconds
		) {
			return item;
		}

		return {
			...item,
			fadeInDurationInSeconds: videoIn,
			fadeOutDurationInSeconds: videoOut,
			audioFadeInDurationInSeconds: audioIn,
			audioFadeOutDurationInSeconds: audioOut,
		};
	}

	if (
		item.type === 'gif' ||
		item.type === 'text' ||
		item.type === 'solid' ||
		item.type === 'image'
	) {
		const {in: fadeIn, out: fadeOut} = clampPair(
			item.fadeInDurationInSeconds,
			item.fadeOutDurationInSeconds,
			preferSide,
		);

		if (
			fadeIn === item.fadeInDurationInSeconds &&
			fadeOut === item.fadeOutDurationInSeconds
		) {
			return item;
		}

		return {
			...item,
			fadeInDurationInSeconds: fadeIn,
			fadeOutDurationInSeconds: fadeOut,
		};
	}

	if (item.type === 'captions') {
		return item;
	}

	throw new Error('Invalid item type: ' + (item satisfies never));
};
