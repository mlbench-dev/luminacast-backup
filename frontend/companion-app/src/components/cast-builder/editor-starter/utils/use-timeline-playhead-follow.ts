import {PlayerRef} from '@remotion/player';
import {useEffect, useRef} from 'react';
import {SCROLL_EDGE_THRESHOLD, TIMELINE_HORIZONTAL_PADDING} from '../constants';
import {useIsPlaying} from '../playback-controls/use-is-playing';
import {SIDE_PANEL_WIDTH} from '../timeline/timeline-side-panel/timeline-side-panel';
import {getItemLeftOffset} from './position-utils';
import {timelineScrollContainerRef} from './restore-scroll-after-zoom';
import {useTimelinePosition} from './use-timeline-position';

export const useFollowPlayheadWhilePlaying = ({
	playerRef,
	timelineWidth,
	visibleFrames,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
	timelineWidth: number | null;
	visibleFrames: number;
}) => {
	const timelinePosition = useTimelinePosition({playerRef});
	const isScrollingRef = useRef(false);
	const isPlaying = useIsPlaying(playerRef);

	useEffect(() => {
		if (!isPlaying) {
			return;
		}

		if (isScrollingRef.current) {
			return;
		}

		const scrollContainer = timelineScrollContainerRef.current;
		if (!scrollContainer) {
			throw new Error('Scroll container not found');
		}

		if (timelineWidth === null) {
			return;
		}

		// Calculate playhead's absolute position in the timeline
		const playheadLeft =
			getItemLeftOffset({
				from: timelinePosition,
				totalDurationInFrames: visibleFrames,
				timelineWidth,
			}) + TIMELINE_HORIZONTAL_PADDING;

		// Get scroll container dimensions and position
		const containerScrollLeft = scrollContainer.scrollLeft;
		const containerWidth = scrollContainer.clientWidth;

		// Calculate playhead position relative to the visible area
		const playheadRelativeToViewport =
			playheadLeft - containerScrollLeft + SIDE_PANEL_WIDTH;

		// Check if playhead is within followThreshold pixels of the right edge
		if (playheadRelativeToViewport > containerWidth - SCROLL_EDGE_THRESHOLD) {
			const targetScrollLeft = playheadLeft - containerWidth / 2;
			const maxScrollLeft = scrollContainer.scrollWidth - containerWidth;
			const clampedScrollLeft = Math.max(
				0,
				Math.min(maxScrollLeft, targetScrollLeft),
			);

			if (clampedScrollLeft !== containerScrollLeft) {
				isScrollingRef.current = true;
				scrollContainer.scrollTo({
					left: clampedScrollLeft,
					behavior: 'smooth',
				});

				// Reset scrolling flag after animation completes
				setTimeout(() => {
					isScrollingRef.current = false;
				}, 300);
			}
		}
	}, [timelinePosition, timelineWidth, visibleFrames, isPlaying]);
};
