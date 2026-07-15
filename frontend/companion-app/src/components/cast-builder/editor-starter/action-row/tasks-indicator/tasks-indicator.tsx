import * as Popover from '@radix-ui/react-popover';
import React, {useEffect, useImperativeHandle, useMemo, useState} from 'react';
import {getDownloadTasks} from '../../caching/download-tasks';
import {
	useDownloadProgresses,
	useLocalUrls,
} from '../../caching/load-to-blob-url';
import {FEATURE_WARN_ON_LONG_RUNNING_PROCESS_IN_PROGRESS} from '../../flags';
import {clsx} from '../../utils/clsx';
import {
	useAssets,
	useAssetStatus,
	useCaptionState,
	useRendering,
} from '../../utils/use-context';
import {assetContextToAssetTasks} from './asset-uploads-to-task';
import {CircularTaskIndicator} from './circular-task-indicator';
import {getTasks, Task} from './get-tasks';
import {getTasksLabel} from './get-tasks-label';
import {TasksContent} from './tasks-content';

export const taskIndicatorRef = React.createRef<{
	open: () => void;
	close: () => void;
}>();

const InnerTasksIndicator: React.FC<{
	tasks: Task[];
	isOpen: boolean;
	setIsOpen: (isOpen: boolean) => void;
}> = ({tasks, isOpen, setIsOpen}) => {
	const {label, progress: progressLabel} = getTasksLabel(tasks);

	useEffect(() => {
		if (!FEATURE_WARN_ON_LONG_RUNNING_PROCESS_IN_PROGRESS) {
			return;
		}

		if (
			progressLabel.type !== 'numeric-progress' &&
			progressLabel.type !== 'working'
		) {
			return;
		}

		window.onbeforeunload = () => {
			return 'Are you sure you want to leave?';
		};

		return () => {
			window.onbeforeunload = null;
		};
	}, [progressLabel.type]);

	return (
		<Popover.Root open={isOpen} onOpenChange={setIsOpen}>
			<Popover.Trigger asChild>
				<button
					type="button"
					className="editor-starter-focus-ring w-[200px]"
					aria-label="Tasks"
				>
					<div
						className={clsx(
							'flex cursor-default items-center gap-3 rounded px-2 py-2 pr-3 hover:bg-white/5',
							isOpen && 'bg-white/105',
						)}
					>
						<CircularTaskIndicator progress={progressLabel} />
						<div className="text-xs text-neutral-400 select-none">{label}</div>
					</div>
				</button>
			</Popover.Trigger>
			<Popover.Portal>
				<Popover.Content side="bottom" align="start">
					{tasks.length > 0 ? <TasksContent tasks={tasks} /> : null}
				</Popover.Content>
			</Popover.Portal>
		</Popover.Root>
	);
};

export const TasksIndicator: React.FC = () => {
	const [isOpen, setIsOpen] = useState(false);
	const renderState = useRendering();
	const captionState = useCaptionState();
	const {assetStatus} = useAssetStatus();
	const {assets} = useAssets();
	const downloadProgresses = useDownloadProgresses();
	const localUrls = useLocalUrls();

	const tasks = useMemo(() => {
		return getTasks({
			renderState,
			captioning: captionState,
			assetUploads: assetContextToAssetTasks({assetStatus, assets}),
			downloadProgresses: getDownloadTasks({
				downloadProgresses,
				assets,
				localUrls,
			}),
		});
	}, [
		renderState,
		captionState,
		assetStatus,
		assets,
		downloadProgresses,
		localUrls,
	]);

	useImperativeHandle(taskIndicatorRef, () => {
		return {
			open: () => {
				setIsOpen(true);
			},
			close: () => {
				setIsOpen(false);
			},
		};
	}, [setIsOpen]);

	if (tasks.length === 0) {
		return null;
	}

	return (
		<InnerTasksIndicator tasks={tasks} isOpen={isOpen} setIsOpen={setIsOpen} />
	);
};
