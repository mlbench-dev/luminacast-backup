import React, {useMemo} from 'react';
import {getVisibleFrames} from '../../utils/get-visible-frames';
import {useFps, useTimelineContext} from '../../utils/use-context';
import {SnapPoint} from '../utils/snap-points';
import {TimelineSnapIndicator} from './timeline-snap-indicator';

export const TimelineSnapIndicators: React.FC<{
	timelineWidth: number;
	activeSnapPoint: SnapPoint;
}> = ({timelineWidth, activeSnapPoint}) => {
	const {durationInFrames} = useTimelineContext();
	const {fps} = useFps();

	const visibleFrames = useMemo(
		() =>
			getVisibleFrames({
				fps,
				totalDurationInFrames: durationInFrames,
			}),
		[fps, durationInFrames],
	);

	return (
		<div className="pointer-events-none absolute inset-0">
			<TimelineSnapIndicator
				snapFrame={activeSnapPoint.frame}
				timelineWidth={timelineWidth}
				visibleFrames={visibleFrames}
			/>
		</div>
	);
};
