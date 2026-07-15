import {DevelopingEllipsis} from '../../captioning/developing-ellipsis';
import {formatBytes} from '../../utils/format-bytes';
import {ProgressType} from './circular-task-indicator';
import {Task} from './get-tasks';

export const getTasksLabel = (
	tasks: Task[],
): {label: React.ReactNode; progress: ProgressType} => {
	const renderTasks = tasks.filter((task) => task.type === 'rendering');
	const captioningTasks = tasks.filter((task) => task.type === 'captioning');
	const assetUploadTasks = tasks.filter((task) => task.type === 'uploading');
	const assetDownloadTasks = tasks.filter(
		(task) => task.type === 'downloading',
	);

	const failedRenderTasks = renderTasks.filter(
		(task) => task.status.type === 'error',
	);
	const failedCaptioningTasks = captioningTasks.filter(
		(task) => task.status.type === 'error',
	);

	if (failedRenderTasks.length > 0 && failedCaptioningTasks.length > 0) {
		return {label: 'Render & captions failed', progress: {type: 'error'}};
	}
	if (failedRenderTasks.length > 0) {
		return {label: 'Render failed', progress: {type: 'error'}};
	}
	if (failedCaptioningTasks.length > 0) {
		return {label: 'Captioning failed', progress: {type: 'error'}};
	}

	const inProgressRenderTasks = renderTasks.filter(
		(task) =>
			task.status.type === 'in-progress' ||
			task.status.type === 'render-initiated',
	);
	const inProgressCaptioningTasks = captioningTasks.filter(
		(task) => task.status.type !== 'done',
	);

	if (assetUploadTasks.length > 0) {
		if (assetUploadTasks.length > 1) {
			return {
				label: `Uploading ${assetUploadTasks.length} files`,
				progress: {
					type: 'numeric-progress',
					progress:
						assetUploadTasks
							.map((a) => {
								return a.status.loadedBytes / a.status.totalBytes;
							})
							.reduce((a, b) => a + b, 0) / assetUploadTasks.length,
				},
			};
		}

		const task = assetUploadTasks[0];
		if (task) {
			return {
				label: `Uploading ${Math.round(task.status.progress * 100)}%`,
				progress: {type: 'numeric-progress', progress: task.status.progress},
			};
		}

		return {
			label: `Uploading ${assetUploadTasks[0].asset.filename}`,
			progress: {type: 'working'},
		};
	}

	if (assetDownloadTasks.length > 0) {
		const task = assetDownloadTasks[0];
		return {
			label: `Downloading ${formatBytes(task.bytesRemaining)}`,
			progress: {
				type: 'numeric-progress',
				progress: 1 - task.bytesRemaining / task.totalSize,
			},
		};
	}

	if (
		inProgressRenderTasks.length > 0 &&
		inProgressCaptioningTasks.length > 0
	) {
		return {
			label: (
				<>
					Captioning and exporting <DevelopingEllipsis />
				</>
			),
			progress: {type: 'working'},
		};
	}

	if (inProgressRenderTasks.length > 0) {
		if (inProgressRenderTasks.length > 1) {
			return {
				label: (
					<>
						Exporting {inProgressRenderTasks.length} files
						<DevelopingEllipsis />
					</>
				),
				progress: {type: 'working'},
			};
		}

		const renderTask = inProgressRenderTasks[0];
		if (renderTask.status.type === 'in-progress') {
			return {
				label: `Exporting ${Math.round(renderTask.status.overallProgress * 100)}%`,
				progress: {
					type: 'numeric-progress',
					progress: renderTask.status.overallProgress,
				},
			};
		}

		return {
			label: (
				<>
					Exporting
					<DevelopingEllipsis />
				</>
			),
			progress: {type: 'working'},
		};
	}
	if (inProgressCaptioningTasks.length > 0) {
		if (inProgressCaptioningTasks.length > 1) {
			return {
				label: (
					<>
						Captioning {inProgressCaptioningTasks.length} files
						<DevelopingEllipsis />
					</>
				),
				progress: {type: 'working'},
			};
		}

		return {
			label: (
				<>
					Captioning in progress
					<DevelopingEllipsis />
				</>
			),
			progress: {type: 'working'},
		};
	}

	const mostRecentTask = tasks[0];

	if (mostRecentTask.type === 'rendering') {
		return {label: 'Export completed', progress: {type: 'success'}};
	}

	if (mostRecentTask.type === 'captioning') {
		return {label: 'Captioning completed', progress: {type: 'success'}};
	}

	throw new Error(`No progress to indicate`);
};
