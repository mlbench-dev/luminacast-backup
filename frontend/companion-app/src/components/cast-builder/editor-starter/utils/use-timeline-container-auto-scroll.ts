import {useCallback, useEffect, useRef} from 'react';
import {MAX_AUTOSCROLL_SPEED} from '../constants';
import {
	sidePanelRef,
	timelineScrollContainerRef,
} from './restore-scroll-after-zoom';

interface UseAutoScrollProps {
	isDragging: boolean;
	edgeThreshold: number;
	maxScrollSpeed: number;
	xAxis: boolean;
	yAxis: boolean;
}

const calculateScrollSpeed = ({
	cursorX,
	leftEdge,
	rightEdge,
	edgeThreshold, // be careful with this, it's both for vertical and horizontal scroll
	maxSpeed = MAX_AUTOSCROLL_SPEED,
}: {
	cursorX: number;
	leftEdge: number;
	rightEdge: number;
	edgeThreshold: number;
	maxSpeed: number;
}): number => {
	if (cursorX < leftEdge + edgeThreshold) {
		const distanceFromEdge = Math.max(0, cursorX - leftEdge);
		const speedMultiplier = 1 - distanceFromEdge / edgeThreshold;
		return -maxSpeed * speedMultiplier;
	}

	if (cursorX > rightEdge - edgeThreshold) {
		const distanceFromEdge = Math.max(0, rightEdge - cursorX);
		const speedMultiplier = 1 - distanceFromEdge / edgeThreshold;
		return maxSpeed * speedMultiplier;
	}

	return 0;
};

const calculateVerticalScrollSpeed = ({
	cursorY,
	topEdge,
	bottomEdge,
	edgeThreshold,
	maxSpeed = MAX_AUTOSCROLL_SPEED,
}: {
	cursorY: number;
	topEdge: number;
	bottomEdge: number;
	edgeThreshold: number;
	maxSpeed: number;
}): number => {
	if (cursorY < topEdge + edgeThreshold) {
		const distanceFromEdge = Math.max(0, cursorY - topEdge);
		const speedMultiplier = 1 - distanceFromEdge / edgeThreshold;
		return -maxSpeed * speedMultiplier;
	}

	if (cursorY > bottomEdge - edgeThreshold) {
		const distanceFromEdge = Math.max(0, bottomEdge - cursorY);
		const speedMultiplier = 1 - distanceFromEdge / edgeThreshold;
		return maxSpeed * speedMultiplier;
	}

	return 0;
};

const applyHorizontalScroll = (
	container: HTMLElement,
	scrollSpeed: number,
): void => {
	const currentScrollLeft = container.scrollLeft;
	const maxScrollLeft = container.scrollWidth - container.clientWidth;

	const newScrollLeft = Math.max(
		0,
		Math.min(maxScrollLeft, currentScrollLeft + scrollSpeed),
	);

	// Only scroll if we're not at the bounds
	if (newScrollLeft !== currentScrollLeft) {
		container.scrollLeft = newScrollLeft;
	}
};

const applyVerticalScroll = (
	container: HTMLElement,
	scrollSpeed: number,
): void => {
	const currentScrollTop = container.scrollTop;
	const maxScrollTop = container.scrollHeight - container.clientHeight;

	const newScrollTop = Math.max(
		0,
		Math.min(maxScrollTop, currentScrollTop + scrollSpeed),
	);

	// Only scroll if we're not at the bounds
	if (newScrollTop !== currentScrollTop) {
		container.scrollTop = newScrollTop;
	}
};

export const useTimelineContainerAutoScroll = ({
	isDragging,
	edgeThreshold,
	maxScrollSpeed,
	xAxis,
	yAxis,
}: UseAutoScrollProps) => {
	const animationFrameRef = useRef<number | null>(null);
	const cursorPositionRef = useRef<{x: number; y: number} | null>(null);

	const cancelAnimation = () => {
		if (animationFrameRef.current) {
			cancelAnimationFrame(animationFrameRef.current);
			animationFrameRef.current = null;
		}
	};

	const startAutoScroll = useCallback(() => {
		const performAutoScroll = () => {
			const scrollContainer = timelineScrollContainerRef.current;
			const sidePanel = sidePanelRef.current;
			const cursorPosition = cursorPositionRef.current;

			if (!scrollContainer || !sidePanel || !cursorPosition) {
				animationFrameRef.current = requestAnimationFrame(performAutoScroll);
				return;
			}

			const containerRect = scrollContainer.getBoundingClientRect();
			const sidePanelRect = sidePanel.getBoundingClientRect();
			const {x: cursorX, y: cursorY} = cursorPosition;

			const leftEdge = sidePanelRect.right;
			const rightEdge = containerRect.right;
			const horizontalScrollSpeed = calculateScrollSpeed({
				cursorX,
				leftEdge,
				rightEdge,
				edgeThreshold,
				maxSpeed: maxScrollSpeed,
			});

			const topEdge = containerRect.top;
			const bottomEdge = containerRect.bottom;
			const verticalScrollSpeed = calculateVerticalScrollSpeed({
				cursorY,
				topEdge,
				bottomEdge,
				edgeThreshold,
				maxSpeed: maxScrollSpeed,
			});

			if (xAxis && horizontalScrollSpeed !== 0) {
				applyHorizontalScroll(scrollContainer, horizontalScrollSpeed);
			}

			if (yAxis && verticalScrollSpeed !== 0) {
				applyVerticalScroll(scrollContainer, verticalScrollSpeed);
			}

			animationFrameRef.current = requestAnimationFrame(performAutoScroll);
		};

		animationFrameRef.current = requestAnimationFrame(performAutoScroll);
	}, [edgeThreshold, maxScrollSpeed, xAxis, yAxis]);

	// Track cursor position when dragging
	useEffect(() => {
		if (!isDragging) {
			cursorPositionRef.current = null;
			return;
		}

		const handlePointerMove = (event: PointerEvent) => {
			cursorPositionRef.current = {
				x: event.clientX,
				y: event.clientY,
			};
		};

		document.addEventListener('pointermove', handlePointerMove);
		return () => {
			document.removeEventListener('pointermove', handlePointerMove);
		};
	}, [isDragging]);

	useEffect(() => {
		if (isDragging) {
			startAutoScroll();
		} else {
			cancelAnimation();
		}

		return cancelAnimation;
	}, [isDragging, startAutoScroll]);

	useEffect(() => {
		return cancelAnimation;
	}, []);
};
