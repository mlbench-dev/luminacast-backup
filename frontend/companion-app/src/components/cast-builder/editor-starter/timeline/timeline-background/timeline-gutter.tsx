import React, {useMemo} from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../../constants';
import {TRACK_DIVIDER_HEIGHT} from '../../state/items';
import {clsx} from '../../utils/clsx';
import {TimelineTrackAndLayout} from '../utils/drag/calculate-track-heights';

type TimelineGutterProps = {
	track: TimelineTrackAndLayout;
	timelineWidth: number;
	inbetweenTrackDropTrackIndex: number | null;
	trackIndex: number;
};

export const TimelineGutter = ({
	track,
	timelineWidth,
	inbetweenTrackDropTrackIndex,
	trackIndex,
}: TimelineGutterProps) => {
	const style: React.CSSProperties = useMemo(() => {
		return {
			height: track.height + TRACK_DIVIDER_HEIGHT,
			width: timelineWidth + TIMELINE_HORIZONTAL_PADDING * 2,
		};
	}, [track, timelineWidth]);

	return (
		<div
			className={clsx(
				'pointer-events-none flex border-b-[1px]',
				inbetweenTrackDropTrackIndex !== null &&
					trackIndex === inbetweenTrackDropTrackIndex - 1
					? 'border-editor-starter-accent'
					: 'border-black/20',
			)}
			style={style}
		/>
	);
};
