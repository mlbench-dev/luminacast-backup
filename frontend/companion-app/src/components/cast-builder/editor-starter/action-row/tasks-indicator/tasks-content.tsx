import {scrollbarStyle} from '../../constants';
import {AssetDownloadProgress} from './asset-download-progress';
import {AssetUploadProgress} from './asset-upload-progress';
import {CaptionProgress} from './caption-progress';
import {Task} from './get-tasks';
import {RenderProgress} from './render-progress';
import {TasksDoneAll} from './tasks-done-all';

export const TasksContent: React.FC<{
	tasks: Task[];
}> = ({tasks}) => {
	return (
		<div
			style={scrollbarStyle}
			className="bg-editor-starter-panel border-editor-starter-border mt-2 max-h-[400px] w-[320px] divide-y divide-white/5 overflow-auto border text-sm text-neutral-300"
		>
			<TasksDoneAll tasks={tasks} />
			{tasks.map((task) => {
				if (task.type === 'rendering') {
					return <RenderProgress key={task.id} renderTask={task} />;
				}

				if (task.type === 'captioning') {
					return <CaptionProgress key={task.id} captionTask={task} />;
				}

				if (task.type === 'uploading') {
					return <AssetUploadProgress key={task.id} assetUploadTask={task} />;
				}

				if (task.type === 'downloading') {
					return (
						<AssetDownloadProgress key={task.id} assetDownloadTask={task} />
					);
				}

				throw new Error(
					`Unknown task type: ${JSON.stringify(task satisfies never)}`,
				);
			})}
		</div>
	);
};
