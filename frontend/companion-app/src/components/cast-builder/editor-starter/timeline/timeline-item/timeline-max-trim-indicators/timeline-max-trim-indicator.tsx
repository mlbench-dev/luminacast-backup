import React from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../../../constants';
import {ItemBeingTrimmed} from '../../../items/trim-indicator';
import {TICKS_HEIGHT} from '../../ticks/constants';

const TimelineMaxTrimIndicatorUnmemoized: React.FC<{
	itemBeingTrimmed: ItemBeingTrimmed;
	timelineWidth: number;
	visibleFrames: number;
	minFrom: number;
	maxDurationInFrames: number;
	top: number;
	height: number;
}> = ({
	itemBeingTrimmed,
	timelineWidth,
	visibleFrames,
	minFrom,
	maxDurationInFrames,
	top,
	height,
}) => {
	const style: React.CSSProperties = React.useMemo(() => {
		const left =
			(minFrom / visibleFrames) * timelineWidth + TIMELINE_HORIZONTAL_PADDING;
		const width = (maxDurationInFrames / visibleFrames) * timelineWidth;
		const maxWidth = timelineWidth - left + TIMELINE_HORIZONTAL_PADDING * 2;

		const itemWouldBeEvenLonger = width > maxWidth;

		return {
			position: 'absolute',
			left,
			top: top + TICKS_HEIGHT,
			width: Math.min(width, maxWidth),
			height: height,
			borderWidth: '2px',
			borderStyle: 'solid',
			...(itemWouldBeEvenLonger
				? {
						borderRightWidth: 0,
						borderTopRightRadius: 0,
						borderBottomRightRadius: 0,
					}
				: {}),
		};
	}, [minFrom, maxDurationInFrames, top, height, visibleFrames, timelineWidth]);

	return (
		<div
			key={itemBeingTrimmed.itemId}
			style={style}
			className="overflow-hidden rounded-sm border-neutral-300"
		/>
	);
};

export const TimelineMaxTrimIndicator = React.memo(
	TimelineMaxTrimIndicatorUnmemoized,
);
