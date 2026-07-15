import {useMemo} from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../../constants';
import {TimelineTrackAndLayout} from '../utils/drag/calculate-track-heights';
import {TimelineGutter} from './timeline-gutter';

export const TimelineBackground: React.FC<{
	tracks: TimelineTrackAndLayout[];
	timelineWidth: number;
	inbetweenTrackDropTrackIndex: number | null;
}> = ({tracks, timelineWidth, inbetweenTrackDropTrackIndex}) => {
	const style: React.CSSProperties = useMemo(() => {
		return {
			width: timelineWidth + TIMELINE_HORIZONTAL_PADDING * 2,
			marginLeft: -TIMELINE_HORIZONTAL_PADDING,
		};
	}, [timelineWidth]);

	return (
		<div className="absolute" style={style}>
			{tracks.map((track, i) => (
				<TimelineGutter
					key={i}
					track={track}
					timelineWidth={timelineWidth}
					inbetweenTrackDropTrackIndex={inbetweenTrackDropTrackIndex}
					trackIndex={i}
				/>
			))}
		</div>
	);
};
