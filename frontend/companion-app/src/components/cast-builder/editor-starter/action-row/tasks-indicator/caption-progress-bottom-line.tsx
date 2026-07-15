import {CaptioningTask} from '../../captioning/caption-state';
import {DevelopingEllipsis} from '../../captioning/developing-ellipsis';
import {formatRelativeTime} from '../../rendering/format-relative-time';
import {useCurrentTime} from '../../rendering/use-current-time';
import {formatBytes} from '../../utils/format-bytes';

export const CaptionProgressBottomLine: React.FC<{
	captionTask: CaptioningTask;
}> = ({captionTask}) => {
	const time = useCurrentTime();
	if (captionTask.status.type === 'done') {
		return (
			<div className="text-xs opacity-50">
				Done
				{' • '}
				{formatRelativeTime({
					timestamp: captionTask.status.doneAt,
					now: time,
				})}
			</div>
		);
	}

	if (captionTask.status.type === 'uploading-audio') {
		return (
			<div className="text-xs opacity-50">
				{formatBytes(captionTask.status.loadedBytes)} /{' '}
				{formatBytes(captionTask.status.totalBytes)} (
				{Math.round(captionTask.status.progress)}%)
			</div>
		);
	}

	if (captionTask.status.type === 'extracting-audio') {
		return (
			<div className="text-xs opacity-50">
				Extracting audio
				<DevelopingEllipsis />
			</div>
		);
	}

	if (captionTask.status.type === 'captioning') {
		return (
			<div className="text-xs opacity-50">
				In progress
				<DevelopingEllipsis />
			</div>
		);
	}

	if (captionTask.status.type === 'error') {
		return (
			<div className="text-xs text-red-300">
				Errored: {captionTask.status.error.message}
			</div>
		);
	}

	throw new Error(
		'Unknown caption task status: ' +
			JSON.stringify(captionTask.status satisfies never),
	);
};
