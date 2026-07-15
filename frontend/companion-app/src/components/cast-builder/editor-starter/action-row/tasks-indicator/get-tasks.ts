import {AssetUploadTask} from '../../assets/assets';
import {AssetDownloadTask} from '../../caching/download-tasks';
import {CaptioningTask} from '../../captioning/caption-state';
import {RenderingTask} from '../../rendering/render-state';
import {truthy} from '../../utils/truthy';

export type Task =
	| CaptioningTask
	| RenderingTask
	| AssetUploadTask
	| AssetDownloadTask;

export const getTasks = ({
	renderState,
	captioning,
	assetUploads,
	downloadProgresses,
}: {
	renderState: RenderingTask[];
	captioning: CaptioningTask[];
	assetUploads: AssetUploadTask[];
	downloadProgresses: AssetDownloadTask | null;
}): Task[] => {
	const renderingAndCaptioning: (RenderingTask | CaptioningTask)[] = [
		...renderState,
		...captioning,
	];
	const newestFirst = renderingAndCaptioning.sort(
		(a, b) => b.startedAt - a.startedAt,
	);

	return [...assetUploads, downloadProgresses, ...newestFirst].filter(truthy);
};
