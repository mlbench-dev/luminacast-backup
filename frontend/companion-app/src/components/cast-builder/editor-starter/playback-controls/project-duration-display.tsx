import {renderFrame} from '../utils/render-frame';
import {useTimelineContext} from '../utils/use-context';

export const ProjectDurationDisplay: React.FC<{
	fps: number;
}> = ({fps}) => {
	const {durationInFrames} = useTimelineContext();

	return (
		<div
			title="Project duration"
			className="text-xs tabular-nums opacity-80 select-none sm:text-sm"
		>
			{renderFrame(durationInFrames, fps)}
		</div>
	);
};
