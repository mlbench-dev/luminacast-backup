import {PlayerRef} from '@remotion/player';
import {renderFrame} from '../utils/render-frame';
import {useTimelinePosition} from '../utils/use-timeline-position';

export function CurrentTimeDisplay({
	playerRef,
	fps,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
	fps: number;
}) {
	const currentFrame = useTimelinePosition({playerRef});

	return (
		<div
			title="Current time"
			className="text-xs tabular-nums opacity-80 select-none sm:text-sm"
		>
			{renderFrame(currentFrame, fps)}
		</div>
	);
}
