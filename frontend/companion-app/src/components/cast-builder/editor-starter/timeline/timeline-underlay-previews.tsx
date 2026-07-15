import React, {useMemo} from 'react';
import {TIMELINE_HORIZONTAL_PADDING} from '../constants';
import {getVisibleFrames} from '../utils/get-visible-frames';
import {
	getItemLeftOffset,
	getItemRoundedPosition,
	getItemWidth,
} from '../utils/position-utils';
import {useFps, useTimelineContext} from '../utils/use-context';
import {Z_TIMELINE_UNDERLAY_PREVIEWS} from '../z-indices';
import {DragPreviewState} from './drag-preview-provider';
import {TICKS_HEIGHT} from './ticks/constants';
import {TimelineTrackAndLayout} from './utils/drag/calculate-track-heights';

export const TimelineUnderlayPreviews: React.FC<{
	timelineWidth: number;
	simulatedTracks: TimelineTrackAndLayout[];
	previewState: DragPreviewState;
}> = ({timelineWidth, simulatedTracks, previewState}) => {
	const {durationInFrames} = useTimelineContext();
	const {fps} = useFps();

	const visibleFrames = useMemo(
		() =>
			getVisibleFrames({
				fps,
				totalDurationInFrames: durationInFrames,
			}),
		[fps, durationInFrames],
	);

	const outerStyle: React.CSSProperties = useMemo(() => {
		return {
			zIndex: Z_TIMELINE_UNDERLAY_PREVIEWS,
		};
	}, []);

	return (
		<div
			className="pointer-events-none absolute inset-0 overflow-hidden"
			style={outerStyle}
		>
			{previewState.positions.map((pos) => {
				const timelineItemLeft = getItemLeftOffset({
					timelineWidth,
					totalDurationInFrames: visibleFrames,
					from: pos.from,
				});
				const timelineItemWidth = getItemWidth({
					itemDurationInFrames: pos.durationInFrames,
					timelineWidth,
					totalDurationInFrames: visibleFrames,
				});
				const {roundedLeft, width: roundedWidth} = getItemRoundedPosition(
					timelineItemLeft,
					timelineItemWidth,
				);

				if (!simulatedTracks[pos.trackIndex]) {
					// eslint-disable-next-line no-console
					console.log(simulatedTracks, pos);
					throw new Error('Track not found - see console for state');
				}

				const top = simulatedTracks[pos.trackIndex].top;
				const height = simulatedTracks[pos.trackIndex].height;

				const style: React.CSSProperties = {
					position: 'absolute',
					left: roundedLeft + TIMELINE_HORIZONTAL_PADDING,
					top: top + TICKS_HEIGHT,
					width: roundedWidth,
					height,
					background: 'rgba(100,100,100,0.4)',
					borderRadius: 4,
				};

				return <div key={pos.id} style={style} />;
			})}
		</div>
	);
};
