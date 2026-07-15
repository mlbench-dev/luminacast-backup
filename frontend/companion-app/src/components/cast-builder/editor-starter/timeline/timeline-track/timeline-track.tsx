import React, {useMemo} from 'react';
import {FEATURE_HIDE_TRACKS, FEATURE_ROLLING_EDITS} from '../../flags';
import {TrackType} from '../../state/types';
import {useAllItems} from '../../utils/use-context';
import {TimelineItem} from '../timeline-item/timeline-item';
import {TimelineTrackRollingEdits} from './timeline-track-rolling-edits';

const TimelineTrackUnmemoized = ({
	track,
	visibleFrames,
	top,
	height,
}: {
	track: TrackType;
	visibleFrames: number;
	top: number;
	height: number;
}) => {
	const {items} = useAllItems();

	const style = useMemo((): React.CSSProperties => {
		if (!FEATURE_HIDE_TRACKS) {
			return {};
		}

		return {
			opacity: track.hidden ? 0.3 : 1,
		};
	}, [track.hidden]);

	return (
		<div className="relative" data-hidden={track.hidden} style={style}>
			{track.items.map((item) => {
				return (
					<TimelineItem
						key={item}
						item={items[item]}
						visibleFrames={visibleFrames}
						top={top}
						height={height}
						trackMuted={track.muted}
					/>
				);
			})}
			{FEATURE_ROLLING_EDITS && (
				<TimelineTrackRollingEdits
					items={track.items}
					allItems={items}
					visibleFrames={visibleFrames}
					top={top}
					height={height}
				/>
			)}
		</div>
	);
};

export const TimelineTrack = React.memo(TimelineTrackUnmemoized);
