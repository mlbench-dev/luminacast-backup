import {PlayerRef} from '@remotion/player';
import {MouseEventHandler, useCallback, useMemo, useState} from 'react';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from '../force-specific-cursor';
import {calculateFrame} from '../timeline/utils/calculate-frame';
import {isLeftClick} from './is-left-click';

export const useTimelineSeek = ({
	containerRef,
	playerRef,
	totalDurationInFrames,
	timelineWidth,
}: {
	containerRef: React.RefObject<HTMLDivElement | null>;
	playerRef: React.RefObject<PlayerRef | null>;
	totalDurationInFrames: number;
	timelineWidth: number | null;
}) => {
	const [isSeekingTimeline, setIsSeekingTimeline] = useState(false);

	const startTimelineSeek: MouseEventHandler<HTMLDivElement> = useCallback(
		(event) => {
			if (!isLeftClick(event)) {
				return;
			}

			if (timelineWidth === null) {
				return;
			}

			event.stopPropagation();
			event.preventDefault();

			forceSpecificCursor('default');
			setIsSeekingTimeline(true);

			if (!containerRef.current) {
				throw new Error('Timeline container not found');
			}

			const handlePointerMove = (e: MouseEvent | React.MouseEvent) => {
				if (!containerRef.current) {
					return;
				}
				if (timelineWidth === null) {
					return;
				}

				e.preventDefault();
				e.stopPropagation();

				const frame = calculateFrame({
					container: containerRef.current,
					xCoordinate: e.clientX,
					totalDurationInFrames,
					timelineWidth,
				});

				playerRef.current?.seekTo(frame);
			};

			const stopDragging = () => {
				setIsSeekingTimeline(false);
				stopForcingSpecificCursor();
				window.removeEventListener('pointermove', handlePointerMove);
				window.removeEventListener('pointerup', stopDragging);
			};

			handlePointerMove(event);
			window.addEventListener('pointermove', handlePointerMove);
			window.addEventListener('pointerup', stopDragging);
		},
		[containerRef, playerRef, totalDurationInFrames, timelineWidth],
	);

	return useMemo(
		() => ({
			isSeekingTimeline,
			startTimelineSeek,
		}),
		[isSeekingTimeline, startTimelineSeek],
	);
};
