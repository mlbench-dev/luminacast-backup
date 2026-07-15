import React from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../../constants';

export const TickHeaders = ({
	children,
	timelineWidth,
}: {
	children: React.ReactNode;
	timelineWidth: number;
}) => {
	const style = React.useMemo<React.CSSProperties>(
		() => ({
			paddingLeft: TIMELINE_HORIZONTAL_PADDING,
			width: timelineWidth + TIMELINE_HORIZONTAL_PADDING * 2,
		}),
		[timelineWidth],
	);

	return (
		<div
			id="tick-headers"
			className="flex overflow-hidden select-none"
			style={style}
		>
			{children}
		</div>
	);
};
