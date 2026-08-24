import React from 'react';
import {isTrackEffectivelyMuted} from '../state/actions/solo-track';
import {TimelineTrack} from './timeline-track/timeline-track';
import {TimelineTrackAndLayout} from './utils/drag/calculate-track-heights';

const TimelineTracksUnmemoized: React.FC<{
	tracks: TimelineTrackAndLayout[];
	visibleFrames: number;
}> = ({tracks, visibleFrames}) => {
	const allTracks = tracks.map((t) => t.track);
	return tracks.map((trackAndLayout) => {
		return (
			<TimelineTrack
				key={trackAndLayout.track.id}
				track={trackAndLayout.track}
				visibleFrames={visibleFrames}
				top={trackAndLayout.top}
				height={trackAndLayout.height}
				effectiveMuted={isTrackEffectivelyMuted(trackAndLayout.track, allTracks)}
			/>
		);
	});
};

export const TimelineTracks = React.memo(TimelineTracksUnmemoized);
