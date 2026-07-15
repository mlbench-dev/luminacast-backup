import {useMemo} from 'react';
import {Z_INDEX_TICKS} from '../../z-indices';
import {useTicks} from '../utils/use-ticks';
import {useTimelineSize} from '../utils/use-timeline-size';
import {TickHeader} from './tick-header';
import {TickHeaders} from './tick-headers';
import {TicksBackground} from './ticks-background';

export const TimelineTicks: React.FC<{
	visibleFrames: number;
	timelineWidth: number;
}> = ({visibleFrames, timelineWidth}) => {
	const {containerWidth} = useTimelineSize();

	if (!containerWidth) {
		throw new Error('Container width is null');
	}

	const {tickMarks} = useTicks({
		visibleFrames,
		timelineWidth,
		containerWidth,
	});

	const style: React.CSSProperties = useMemo(() => {
		return {
			zIndex: Z_INDEX_TICKS,
		};
	}, []);

	return (
		<div className="sticky top-0 bg-editor-starter-panel" style={style}>
			<TicksBackground timelineWidth={timelineWidth} />
			<TickHeaders timelineWidth={timelineWidth}>
				{tickMarks.map((tick, i) => (
					<TickHeader key={i} tick={tick} />
				))}
			</TickHeaders>
		</div>
	);
};
