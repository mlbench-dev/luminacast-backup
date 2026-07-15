import React from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../../constants';
import {Z_INDEX_TIMELINE_SNAP_INDICATOR} from '../../z-indices';

interface TimelineSnapIndicatorProps {
	snapFrame: number;
	timelineWidth: number;
	visibleFrames: number;
}

const TimelineSnapIndicatorUnmemoized: React.FC<TimelineSnapIndicatorProps> = ({
	snapFrame,
	timelineWidth,
	visibleFrames,
}) => {
	const style: React.CSSProperties = React.useMemo(() => {
		const left =
			(snapFrame / visibleFrames) * timelineWidth + TIMELINE_HORIZONTAL_PADDING;

		return {
			position: 'absolute',
			left,
			top: 0,
			width: '1px',
			height: '100%',
			pointerEvents: 'none',
			zIndex: Z_INDEX_TIMELINE_SNAP_INDICATOR,
		};
	}, [snapFrame, visibleFrames, timelineWidth]);

	return <div className="bg-neutral-700" style={style} />;
};

export const TimelineSnapIndicator = React.memo(
	TimelineSnapIndicatorUnmemoized,
);
