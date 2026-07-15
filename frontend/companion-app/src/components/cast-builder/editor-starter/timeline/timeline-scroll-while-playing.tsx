import {PlayerRef} from '@remotion/player';
import {useFollowPlayheadWhilePlaying} from '../utils/use-timeline-playhead-follow';

export const TimelineFollowPlayheadWhilePlaying = ({
	playerRef,
	timelineWidth,
	visibleFrames,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
	timelineWidth: number | null;
	visibleFrames: number;
}) => {
	useFollowPlayheadWhilePlaying({playerRef, timelineWidth, visibleFrames});
	return null;
};
