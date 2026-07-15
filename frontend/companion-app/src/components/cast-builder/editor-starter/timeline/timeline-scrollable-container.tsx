import {useMemo} from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../constants';
import {timelineScrollableContainerRef} from '../utils/restore-scroll-after-zoom';
import {useTimelineHeight} from '../utils/use-context';
import {TICKS_HEIGHT} from './ticks/constants';

export const TimelineScrollableContainer = ({
	children,
	timelineWidth,
}: {
	children: React.ReactNode;
	timelineWidth: number;
}) => {
	const timelineHeight = useTimelineHeight();

	const styles = useMemo(
		(): React.CSSProperties => ({
			width: timelineWidth + TIMELINE_HORIZONTAL_PADDING * 2,
			// calc minus the ticks height
			height: timelineHeight - TICKS_HEIGHT,
			paddingLeft: TIMELINE_HORIZONTAL_PADDING,
			paddingRight: TIMELINE_HORIZONTAL_PADDING,
		}),
		[timelineWidth, timelineHeight],
	);

	return (
		<div ref={timelineScrollableContainerRef} style={styles}>
			{children}
		</div>
	);
};
