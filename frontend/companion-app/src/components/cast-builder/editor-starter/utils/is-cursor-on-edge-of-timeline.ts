import {
	timelineContainerRef,
	timelineRightSide,
	timelineScrollContainerRef,
} from './restore-scroll-after-zoom';

const RIGHT_EDGE_SCROLL_THRESHOLD = 40;
const LEFT_EDGE_SCROLL_THRESHOLD = 0; // you can adjust this

export const isCursorOnEdgeOfTimeline = (cursorX: number) => {
	const currentTimeline = timelineRightSide.current;
	if (!currentTimeline) {
		throw new Error('Timeline right side not found');
	}

	const timelineContainer = timelineContainerRef.current;
	if (!timelineContainer) {
		throw new Error('Timeline container not found');
	}

	const timelineScrollContainer = timelineScrollContainerRef.current;
	if (!timelineScrollContainer) {
		throw new Error('Timeline scroll container not found');
	}

	const isScrolled = timelineScrollContainer.scrollLeft > 0;

	const leftOffset = isScrolled ? LEFT_EDGE_SCROLL_THRESHOLD : 0;

	const isLeft =
		cursorX <
		timelineContainer.getBoundingClientRect().left +
			currentTimeline.offsetLeft +
			leftOffset;
	if (isLeft) {
		return 'left';
	}

	const isRight =
		cursorX >
		timelineScrollContainer.getBoundingClientRect().right -
			RIGHT_EDGE_SCROLL_THRESHOLD;
	if (isRight) {
		return 'right';
	}

	return 'none';
};
