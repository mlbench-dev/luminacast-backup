import React from 'react';
import {TimelineTrack} from './timeline-track/timeline-track';
import {TimelineTrackAndLayout} from './utils/drag/calculate-track-heights';

const TimelineTracksUnmemoized: React.FC<{
	tracks: TimelineTrackAndLayout[];
	visibleFrames: number;
}> = ({tracks, visibleFrames}) => {
	return tracks.map((trackAndLayout) => {
		return (
			<TimelineTrack
				key={trackAndLayout.track.id}
				track={trackAndLayout.track}
				visibleFrames={visibleFrames}
				top={trackAndLayout.top}
				height={trackAndLayout.height}
			/>
		);
	});
};

export const TimelineTracks = React.memo(TimelineTracksUnmemoized);
