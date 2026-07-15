import React, {useMemo} from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../../constants';
import {TICKS_HEIGHT} from './constants';

export const TicksBackground: React.FC<{
	timelineWidth: number;
}> = ({timelineWidth}) => {
	const style = useMemo(() => {
		return {
			height: TICKS_HEIGHT,
			width: timelineWidth + TIMELINE_HORIZONTAL_PADDING * 2,
		};
	}, [timelineWidth]);

	return (
		<div
			style={style}
			className="bg-editor-starter-panel pointer-events-none absolute top-0"
		/>
	);
};
